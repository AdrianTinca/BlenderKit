package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"

	"github.com/google/uuid"
)

func downloadAssetHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Error reading request body: "+err.Error(), http.StatusInternalServerError)
		return
	}
	// Ensure the body is closed after reading
	defer r.Body.Close()

	var downloadData DownloadData
	err = json.Unmarshal(body, &downloadData)
	if err != nil {
		fmt.Println(">>> Error parsing DownloadRequest:", err)
		http.Error(w, "Error parsing JSON: "+err.Error(), http.StatusBadRequest)
		return
	}

	var rJSON map[string]interface{}
	err = json.Unmarshal(body, &rJSON)
	if err != nil {
		fmt.Println(">>> Error parsing JSON:", err)
		http.Error(w, "Error parsing JSON: "+err.Error(), http.StatusBadRequest)
		return
	}

	taskID := uuid.New().String()
	go doAssetDownload(rJSON, downloadData, taskID)

	// Response to add-on
	resData := map[string]string{"task_id": taskID}
	responseJSON, err := json.Marshal(resData)
	if err != nil {
		http.Error(w, "Error converting to JSON: "+err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(responseJSON)
}

func doAssetDownload(origJSON map[string]interface{}, data DownloadData, taskID string) {
	TasksMux.Lock()
	task := NewTask(origJSON, data.AppID, taskID, "asset_download")
	task.Message = "Getting download URL"
	Tasks[task.AppID][taskID] = task
	TasksMux.Unlock()

	// GET URL FOR BLEND FILE WITH CORRECT RESOLUTION
	canDownload, downloadURL, err := GetDownloadURL(data)
	if err != nil {
		TaskErrorCh <- &TaskError{
			AppID:  data.AppID,
			TaskID: taskID,
			Error:  err}
		return
	}
	if !canDownload {
		TaskErrorCh <- &TaskError{
			AppID:  data.AppID,
			TaskID: taskID,
			Error:  fmt.Errorf("user cannot download this file")}
		return
	}

	// EXTRACT FILENAME FROM URL
	TaskProgressUpdateCh <- &TaskProgressUpdate{
		AppID:    data.AppID,
		TaskID:   taskID,
		Progress: 0,
		Message:  "Extracting filename",
	}
	fileName, err := ExtractFilenameFromURL(downloadURL)
	if err != nil {
		TaskErrorCh <- &TaskError{
			AppID:  data.AppID,
			TaskID: taskID,
			Error:  err,
		}
	}
	// GET FILEPATHS TO WHICH WE DOWNLOAD
	TaskProgressUpdateCh <- &TaskProgressUpdate{
		AppID:    data.AppID,
		TaskID:   taskID,
		Progress: 0,
		Message:  "Getting filepaths",
	}
	downloadFilePaths := GetDownloadFilepaths(data, fileName)

	// CHECK IF FILE EXISTS ON HARD DRIVE
	TaskProgressUpdateCh <- &TaskProgressUpdate{
		AppID:    data.AppID,
		TaskID:   taskID,
		Progress: 0,
		Message:  "Checking files on disk",
	}
	existingFiles := 0
	for _, filePath := range downloadFilePaths {
		exists, info, err := FileExists(filePath)
		if err != nil {
			if info.IsDir() {
				fmt.Println("Deleting directory:", filePath)
				err := os.RemoveAll(filePath)
				if err != nil {
					fmt.Println("Error deleting directory:", err)
				}
			} else {
				fmt.Println("Error checking if file exists:", err)
			}
			continue
		}
		if exists {
			existingFiles++
		}
	}

	action := ""
	if existingFiles == 0 { // No existing files -> download
		action = "download"
	} else if existingFiles == 2 { // Both files exist -> skip download
		action = "place"
	} else if existingFiles == 1 && len(downloadFilePaths) == 2 { // One file exists, but there are two download paths -> sync the missing file
		// TODO: sync the missing file
		action = "sync"
	} else if existingFiles == 1 && len(downloadFilePaths) == 1 { // One file exists, and there is only one download path -> skip download
		action = "place"
	} else { // Something unexpected happened -> delete and download
		log.Println("Unexpected number of existing files:", existingFiles)
		for _, file := range downloadFilePaths {
			err := DeleteFile(file)
			if err != nil {
				log.Println("Error deleting file:", err)
			}
		}
	}

	// START DOWNLOAD IF NEEDED
	if action == "download" {
		fp := downloadFilePaths[0]
		err = downloadAsset(downloadURL, fp, data, taskID, task.Ctx)
		if err != nil {
			e := fmt.Errorf("error downloading asset: %v", err)
			TaskErrorCh <- &TaskError{
				AppID:  data.AppID,
				TaskID: taskID,
				Error:  e,
			}
			return
		}
	} else {
		fmt.Println("PLACING THE FILE")
	}

	if data.UnpackFiles {
		// TODO: UNPACK FILE
	}

	result := map[string]interface{}{"file_paths": downloadFilePaths}
	TaskFinishCh <- &TaskFinish{
		AppID:   data.AppID,
		TaskID:  taskID,
		Message: "Asset downloaded and ready",
		Result:  result,
	}
}

func downloadAsset(url, filePath string, data DownloadData, taskID string, ctx context.Context) error {
	file, err := os.Create(filePath)
	if err != nil {
		return err
	}
	defer file.Close()

	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return err
	}

	req.Header = getHeaders("", *SystemID) // download needs no API key in headers
	resp, err := ClientDownloads.Do(req)
	if err != nil {
		e := DeleteFile(filePath)
		if e != nil {
			return fmt.Errorf("request failed: %v, failed to delete file: %v", err, e)
		}
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		err := fmt.Errorf("server returned non-OK status: %d", resp.StatusCode)
		e := DeleteFile(filePath)
		if e != nil {
			return fmt.Errorf("%v, failed to delete file: %v", err, e)
		}
		return err
	}

	totalLength := resp.Header.Get("Content-Length")
	if totalLength == "" {
		e := DeleteFile(filePath)
		if e != nil {
			return fmt.Errorf("request failed: %v, failed to delete file: %v", err, e)
		}
		return fmt.Errorf("Content-Length header is missing")
	}

	fileSize, err := strconv.ParseInt(totalLength, 10, 64)
	if err != nil {
		e := DeleteFile(filePath)
		if e != nil {
			return fmt.Errorf("length conversion failed: %v, failed to delete file: %v", err, e)
		}
		return err
	}

	// Setup for monitoring progress and cancellation
	sizeInMB := float64(fileSize) / 1024 / 1024
	var downloaded int64 = 0
	progress := make(chan int64)
	go func() {
		var downloadMessage string
		for p := range progress {
			progress := int(100 * p / fileSize)
			if sizeInMB < 1 { // If the size is less than 1MB, show in KB
				downloadMessage = fmt.Sprintf("Downloading %dkB (%d%%)", int(sizeInMB*1024), progress)
			} else { // If the size is not a whole number, show one decimal place
				downloadMessage = fmt.Sprintf("Downloading %.1fMB (%d%%)", sizeInMB, progress)
			}
			TaskProgressUpdateCh <- &TaskProgressUpdate{
				AppID:    data.AppID,
				TaskID:   taskID,
				Progress: progress,
				Message:  downloadMessage,
			}
		}
	}()

	buffer := make([]byte, 32*1024) // 32KB buffer
	for {
		select {
		case <-ctx.Done():
			close(progress)
			err = DeleteFile(filePath)
			if err != nil {
				return fmt.Errorf("%v, failed to delete file: %v", ctx.Err(), err)
			}
			return ctx.Err()
		default:
			n, readErr := resp.Body.Read(buffer)
			if n > 0 {
				_, writeErr := file.Write(buffer[:n])
				if writeErr != nil {
					close(progress)
					err = DeleteFile(filePath) // Clean up; ignore error from DeleteFile to focus on writeErr
					if err != nil {
						return fmt.Errorf("%v, failed to delete file: %v", writeErr, err)
					}
					return writeErr
				}
				downloaded += int64(n)
				progress <- downloaded
			}
			if readErr != nil {
				close(progress)
				if readErr == io.EOF {
					return nil // Download completed successfully
				}
				err := DeleteFile(filePath) // Clean up; ignore error from DeleteFile to focus on readErr
				if err != nil {
					return fmt.Errorf("%v, failed to delete file: %v", readErr, err)
				}
				return readErr
			}
		}
	}
}

// should return ['/Users/ag/blenderkit_data/models/kitten_0992088b-fb84-4c69-bb6e-426272970c8b/kitten_2K_d5368c9d-092e-4319-afe1-dd765de6da01.blend']
func GetDownloadFilepaths(data DownloadData, filename string) []string {
	filePaths := []string{}
	filename = ServerToLocalFilename(filename, data.AssetData.Name)
	assetFolderName := fmt.Sprintf("%s_%s", Slugify(data.AssetData.Name), data.AssetData.ID)
	for _, dir := range data.DownloadDirs {
		assetDirPath := filepath.Join(dir, assetFolderName)
		if _, err := os.Stat(assetDirPath); os.IsNotExist(err) {
			os.MkdirAll(assetDirPath, os.ModePerm)
		}
		filePath := filepath.Join(assetDirPath, filename)
		filePaths = append(filePaths, filePath)
	}
	// TODO: check on Windows if path is not too long
	return filePaths
}

func GetDownloadURL(data DownloadData) (bool, string, error) {
	reqData := url.Values{}
	reqData.Set("scene_uuid", data.SceneID)

	file, _ := GetResolutionFile(data.Files, data.Resolution)

	req, err := http.NewRequest("GET", file.DownloadURL, nil)
	if err != nil {
		return false, "", err
	}
	req.Header = getHeaders(data.APIKey, *SystemID)
	req.URL.RawQuery = reqData.Encode()

	resp, err := ClientAPI.Do(req)
	if err != nil {
		return false, "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return false, "", fmt.Errorf("server returned non-OK status: %d", resp.StatusCode)
	}

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return false, "", err
	}

	var respJSON map[string]interface{}
	err = json.Unmarshal(bodyBytes, &respJSON)
	if err != nil {
		return false, "", err
	}

	url, ok := respJSON["filePath"].(string)
	if !ok || url == "" {
		return false, "", fmt.Errorf("filePath is None or invalid")
	}

	return true, url, nil
}

func GetResolutionFile(files []File, targetRes string) (File, string) {
	resolutionsMap := map[string]int{
		"resolution_0_5K": 512,
		"resolution_1K":   1024,
		"resolution_2K":   2048,
		"resolution_4K":   4096,
		"resolution_8K":   8192,
	}
	var originalFile, closest File
	var targetResInt, mindist = resolutionsMap[targetRes], 100000000

	fmt.Println(">>> Target resolution:", targetRes)
	for _, f := range files {
		fmt.Println(">>> File type:", f.FileType)
		if f.FileType == "thumbnail" {
			continue
		}
		if f.FileType == "blend" {
			originalFile = f
			if targetRes == "ORIGINAL" {
				return f, "blend"
			}
		}

		r := strconv.Itoa(resolutionsMap[f.FileType])
		if r == targetRes {
			return f, f.FileType // exact match found, return.
		}

		// TODO: check if this works properly
		// find closest resolution if the exact match won't be found
		rval, ok := resolutionsMap[f.FileType]
		if ok && targetResInt != 0 {
			rdiff := abs(targetResInt - rval)
			if rdiff < mindist {
				closest = f
				mindist = rdiff
			}
		}
	}

	if (closest != File{}) {
		return closest, closest.FileType
	}

	return originalFile, "blend"
}
