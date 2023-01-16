import logging
import os
import platform
import subprocess
import sys
from os import environ, path
from urllib.parse import urlparse

import bpy
import requests

from . import dependencies, global_vars, reports


bk_logger = logging.getLogger(__name__)
TIMEOUT = (0.1, 0.5) 


def get_address() -> str:
  """Get address of the daemon."""
  return f'http://127.0.0.1:{get_port()}'


def get_port() -> str:
  """Get the most probable port of currently running daemon.
  After add-on registration and if all goes well, the port is the same as 
  """
  return global_vars.DAEMON_PORTS[0]


def reorder_ports(port: str):
    """Reorder DAEMON_PORTS so the specified port is first."""
    i = global_vars.DAEMON_PORTS.index(port)
    global_vars.DAEMON_PORTS = global_vars.DAEMON_PORTS[i:] + global_vars.DAEMON_PORTS[:i]


def get_daemon_directory_path() -> str:
  """Get path to daemon directory in blenderkit_data directory."""
  global_dir = bpy.context.preferences.addons['blenderkit'].preferences.global_dir
  directory = path.join(global_dir, 'daemon')
  return path.abspath(directory)


def get_reports(app_id: str, api_key=''):
    """Get reports for all tasks of app_id Blender instance at once.
    If few last calls failed, then try to get reports also from other than default ports.
    """
    data = {'app_id': app_id, 'api_key': api_key}
    if global_vars.DAEMON_FAILED_REPORTS < 10: #on 10, there is second daemon start
        url = f'{get_address()}/report'
        report = request_report(url, data)
        return report

    last_exception = None
    for port in global_vars.DAEMON_PORTS:
        url =  f'http://127.0.0.1:{port}/report'
        try:
            report = request_report(url, data)
            bk_logger.warning(f'Got reports port {port}, setting it as default for this instance')
            reorder_ports(port)
            return report
        except Exception as e:
            print(f'Failed to get reports on: {e}')
            last_exception = e
    raise last_exception


def request_report(url: str, data: dict):
    with requests.Session() as session:
        resp = session.get(url, json=data, timeout=TIMEOUT, proxies={})
        return resp.json()


def search_asset(data):
  """Search for specified asset."""
  bk_logger.debug('Starting search request')
  address = get_address()
  data['app_id'] = os.getpid()
  with requests.Session() as session:
    url = address + "/search_asset"
    resp = session.post(url, json=data, timeout=TIMEOUT, proxies={})
    bk_logger.debug('Got search response')
    return resp.json()


def download_asset(data):
  """Download specified asset."""
  address = get_address()
  data['app_id'] = os.getpid()
  with requests.Session() as session:
    url = address + "/download_asset"
    resp = session.post(url, json=data, timeout=TIMEOUT, proxies={})
    return resp.json()


def upload_asset(upload_data, export_data, upload_set):
  """Upload specified asset."""
  data = {
    'app_id': os.getpid(),
    'upload_data': upload_data,
    'export_data': export_data,
    'upload_set': upload_set,
  }
  with requests.Session() as session:
    url = get_address() + "/upload_asset"
    bk_logger.debug(f"making a request to: {url}")
    resp = session.post(url, json=data, timeout=TIMEOUT, proxies={})
    return resp.json()


def kill_download(task_id):
  """Kill the specified task with ID on the daemon."""
  address = get_address()
  with requests.Session() as session:
    url = address + "/kill_download"
    resp = session.get(url, json={'task_id': task_id}, timeout=TIMEOUT, proxies={})
    return resp


### PROFILES
def fetch_gravatar_image(author_data):
  """Fetch gravatar image for specified user. Find it on disk or download it from server."""
  author_data['app_id'] = os.getpid()
  with requests.Session() as session:
    return session.get(f'{get_address()}/profiles/fetch_gravatar_image', json=author_data)

def get_user_profile(api_key):
  """Get profile of currently logged-in user. This creates task to daemon to fetch data which are later handled once available."""
  data = {'api_key': api_key, 'app_id': os.getpid()}
  with requests.Session() as session:
    return session.get(f'{get_address()}/profiles/get_user_profile', json=data)


### COMMENTS
def get_comments(asset_id, api_key=''):
  """Get all comments on the asset."""
  data = {
    'asset_id': asset_id,
    'api_key': api_key,
    'app_id': os.getpid(),
    }
  with requests.Session() as session:
    return session.post(f'{get_address()}/comments/get_comments', json=data)

def create_comment(asset_id, comment_text, api_key, reply_to_id=0):
  """Create a new comment."""
  data = {
    'asset_id': asset_id,
    'comment_text': comment_text,
    'api_key': api_key,
    'reply_to_id': reply_to_id,
    'app_id': os.getpid(),
    }
  with requests.Session() as session:
    return session.post(f'{get_address()}/comments/create_comment', json=data)

def feedback_comment(asset_id, comment_id, api_key, flag='like'):
  """Feedback the comment - by default with like. Other flags can be used also."""
  data = {
    'asset_id': asset_id,
    'comment_id': comment_id,
    'api_key': api_key,
    'flag': flag,
    'app_id': os.getpid(),
    }
  with requests.Session() as session:
    return session.post(f'{get_address()}/comments/feedback_comment', json=data)

def mark_comment_private(asset_id, comment_id, api_key, is_private=False):
  """Mark the comment as private or public."""
  data = {
    'asset_id': asset_id,
    'comment_id': comment_id,
    'api_key': api_key,
    'is_private': is_private,
    'app_id': os.getpid(),
    }
  with requests.Session() as session:
    return session.post(f'{get_address()}/comments/mark_comment_private', json=data)

### NOTIFICATIONS
def mark_notification_read(notification_id):
  """Mark the notification as read on the server."""
  data = {
    'notification_id': notification_id,
    'api_key': bpy.context.preferences.addons['blenderkit'].preferences.api_key,
    'app_id': os.getpid(),
    }
  with requests.Session() as session:
    return session.post(f'{get_address()}/notifications/mark_notification_read', json=data)

### REPORTS
def report_usages(report: dict):
  """Report usages of assets in current scene via daemon to the server."""
  report['api_key'] = bpy.context.preferences.addons['blenderkit'].preferences.api_key
  report['app_id'] = os.getpid()
  with requests.Session() as session:
    resp = session.post(f'{get_address()}/report_usages', json=report)
    return resp


### AUTHORIZATION
def send_code_verifier(code_verifier: str):
  data = {'code_verifier': code_verifier}
  with requests.Session() as session:
    resp = session.post(f'{get_address()}/code_verifier', json=data, timeout=TIMEOUT, proxies={})
    return resp


### WRAPPERS
def get_download_url(asset_data, scene_id, api_key):
  data = {
    'app_id': os.getpid(),
    'resolution': 'blend',
    'asset_data': asset_data,
    'PREFS': {
      'api_key': api_key,
      'scene_id': scene_id,
    },
  }
  with requests.Session() as session:
    url = get_address() + "/wrappers/get_download_url"
    resp = session.get(url, json=data)
    resp = resp.json()
    return (resp['has_url'], resp['asset_data'])


def refresh_token(refresh_token):
  """Refresh authentication token."""
  with requests.Session() as session:
    url = get_address() + "/refresh_token"
    resp = session.get(url, json={'refresh_token': refresh_token}, timeout=TIMEOUT, proxies={})
    return resp


def daemon_is_alive(session: requests.Session) -> tuple[bool, str]:
  """Check whether daemon is responding."""
  address = get_address()
  try:
    with session.get(address, timeout=TIMEOUT, proxies={}) as resp:
      if resp.status_code != 200:
        return False, f'Server response not 200: {resp.status_code}'
      return True, f'Server alive, PID: {resp.text}'

  except requests.exceptions.ConnectionError as err:
    return False, f'EXCEPTION OCCURED:", {err}, {type(err)}'


def report_blender_quit():
  address = get_address()
  with requests.Session() as session:
    url = address + "/report_blender_quit"
    resp = session.get(url, json={'app_id':os.getpid()}, timeout=TIMEOUT, proxies={})
    return resp


def kill_daemon_server():
  """Request to restart the daemon server."""
  address = get_address()
  with requests.Session() as session:
    url = address + "/shutdown"
    resp = session.get(url, timeout=TIMEOUT, proxies={})
    return resp


def handle_daemon_status_task(task):
  bk_server_status = task.result['online_status']
  if bk_server_status == 200:
    if global_vars.DAEMON_ONLINE == False:
      reports.add_report(f'Connected to {urlparse(global_vars.SERVER).netloc}')
      wm = bpy.context.window_manager
      wm.blenderkitUI.logo_status = "logo"
      global_vars.DAEMON_ONLINE = True
    return

  if global_vars.DAEMON_ONLINE == True:
    if bk_server_status == 429:
      reports.add_report(f'API limit exceeded for {urlparse(global_vars.SERVER).netloc}', timeout=10, type='ERROR')
    else:
      reports.add_report(f'Disconnected from {urlparse(global_vars.SERVER).netloc}', timeout=10, type='ERROR')
    wm = bpy.context.window_manager
    wm.blenderkitUI.logo_status = "logo_offline"
    global_vars.DAEMON_ONLINE = False


def check_daemon_exit_code() -> tuple[int, str]:
  """Checks the exit code of daemon process. Returns exit_code and its message.
  Function polls the process which should not block,
  but better run only when daemon misbehaves and is expected that it already exited.
  """
  exit_code = global_vars.daemon_process.poll()
  if exit_code is None:
    return exit_code, "Daemon process is running."
  
  #exit_code = global_vars.daemon_process.returncode
  log_path = f'{get_daemon_directory_path()}/daemon-{get_port()}.log'
  if exit_code == 101:
    message = f'failed to import AIOHTTP. Try to delete {dependencies.get_dependencies_path()} and restart Blender.'
  elif exit_code == 102:
    message = f'failed to import CERTIFI. Try to delete {dependencies.get_dependencies_path()} and restart Blender.'
  elif exit_code == 100:
    message = f'unexpected OSError. Please report a bug and paste content of log {log_path}'
  elif exit_code == 111:
    message = 'unable to bind any socket. Check your antivirus/firewall and unblock BlenderKit.'
  elif exit_code == 113:
    message = 'cannot open port. Check your antivirus/firewall and unblock BlenderKit.'
  elif exit_code == 114:
    message = f'invalid pointer address. Please report a bug and paste content of log {log_path}'
  elif exit_code == 121:
    message = 'semaphore timeout exceeded. In preferences set IP version to "Use only IPv4".'
  elif exit_code == 148:
    message = 'address already in use. Select different daemon port in preferences.'
  elif exit_code == 149:
    message = 'address already in use. Select different daemon port in preferences.'
  else:
    message = f'unexpected Exception. Please report a bug and paste content of log {log_path}'

  return exit_code, message


def start_daemon_server():
  """Start daemon server in separate process."""
  daemon_dir = get_daemon_directory_path()
  log_path = f'{daemon_dir}/daemon-{get_port()}.log'
  blenderkit_path = path.dirname(__file__)
  daemon_path = path.join(blenderkit_path, 'daemon/daemon.py')
  preinstalled_deps = dependencies.get_preinstalled_deps_path()
  installed_deps = dependencies.get_installed_deps_path()

  env  = environ.copy()
  env['PYTHONPATH'] = installed_deps + os.pathsep + preinstalled_deps

  python_home = path.abspath(path.dirname(sys.executable) + "/..")
  env['PYTHONHOME'] = python_home
  
  creation_flags = 0
  if platform.system() == "Windows":
    env['PATH'] = env['PATH'] + os.pathsep + path.abspath(path.dirname(sys.executable) + "/../../../blender.crt")
    creation_flags = subprocess.CREATE_NO_WINDOW

  python_check = subprocess.run(args=[sys.executable, "--version"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  if python_check.returncode != 0:
    bk_logger.warning(
      f"Error checking Python interpreter, exit code: {python_check.returncode}," +
      f"Stdout: {python_check.stdout}, " +
      f"Stderr: {python_check.stderr}, " +
      f"Where Python: {sys.executable}, " +
      f"Environment: {env}"
    )

  try:
    with open(log_path, "wb") as log:
      global_vars.daemon_process = subprocess.Popen(
        args = [
          sys.executable,
          '-u', daemon_path,
          '--port', get_port(),
          '--server', global_vars.SERVER,
          '--proxy_which', global_vars.PREFS.get('proxy_which'),
          '--proxy_address', global_vars.PREFS.get('proxy_address'),
          '--proxy_ca_certs', global_vars.PREFS.get('proxy_ca_certs'),
          '--ip_version', global_vars.PREFS.get('ip_version'),
          '--system_id', bpy.context.preferences.addons['blenderkit'].preferences.system_id,
          '--version', f'{global_vars.VERSION[0]}.{global_vars.VERSION[1]}.{global_vars.VERSION[2]}.{global_vars.VERSION[3]}',
        ],
        env           = env,
        stdout        = log,
        stderr        = log,
        creationflags = creation_flags,
      )
  except PermissionError as e:
    reports.add_report(f"FATAL ERROR: Write access denied to {daemon_dir}. Check you have write permissions to the directory.", 10, 'ERROR')
    raise(e)
  except OSError as e:
    if platform.system() != "Windows":
      reports.add_report(str(e), 10, 'ERROR')
      raise(e)
    if e.winerror == 87: # parameter is incorrect, issue #100
      error_message = f"FATAL ERROR: Daemon server blocked from starting. Please check your antivirus or firewall. Error: {e}"
      reports.add_report(error_message, 10, 'ERROR')
      raise(e)
    else:
      reports.add_report(str(e), 10, 'ERROR')
      raise(e)
  except Exception as e:
    reports.add_report(f"Error: Daemon server failed to start - {e}", 10, 'ERROR')
    raise(e)

  if python_check.returncode == 0:
    bk_logger.info(f'Daemon server starting on address {get_address()}, log file for errors located at: {log_path}')
  else:
    bk_logger.warning(f'Tried to start daemon server on address {get_address()}, PID: {global_vars.daemon_process.pid},\nlog file located at: {log_path}')
    reports.add_report(f'Due to unsuccessful Python check the daemon server will probably fail to run. Please report a bug at BlenderKit.', 5, 'ERROR')

