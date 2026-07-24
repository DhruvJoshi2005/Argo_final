# download_bulk.py
import requests
from bs4 import BeautifulSoup
import os
from datetime import datetime
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Parameters
base_url = 'https://data-argo.ifremer.fr/dac/incois/?C=M;O=D'
download_dir_base = 'data_downloads'
log_json = 'download_log.json'

# File types and folders
file_types = {
    '_meta.nc': 'meta',
    '_prof.nc': 'prof',
    '_Sprof.nc': 'sprof',
    '_tech.nc': 'tech',
    '_Rtraj.nc': 'rtraj'
}

# Setup session with retry logic
session = requests.Session()
retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)

def load_download_log():
    if os.path.exists(log_json):
        with open(log_json, 'r') as f:
            return json.load(f)
    return {}

def save_download_log(log_data):
    with open(log_json, 'w') as f:
        json.dump(log_data, f, indent=2)

def download_file(file_url, dest_path, retries=3):
    filename = os.path.basename(dest_path)
    for attempt in range(1, retries + 1):
        try:
            headers = {}
            if os.path.exists(dest_path):
                pos = os.path.getsize(dest_path)
                if pos > 0:
                    headers["Range"] = f"bytes={pos}-"

            with session.get(file_url, headers=headers, stream=True, timeout=(10, 120)) as r:
                # 416 means we asked past the end: the file is already complete.
                if r.status_code == 416:
                    return True

                # Mode comes from the response, not the request: a server that
                # ignores Range replies 200 with the whole file, and appending
                # that onto a partial file would corrupt it.
                if r.status_code == 206:
                    mode = "ab"
                elif r.status_code == 200:
                    mode = "wb"
                else:
                    continue  # unexpected status — let the retry loop try again

                with open(dest_path, mode) as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                return True
        except Exception as e:
            print(f"[WARNING] Attempt {attempt} error for {filename}: {e}")
    return False

def ensure_directories():
    """Check and create download directories before starting downloads."""
    for folder_name in file_types.values():
        dest_folder = os.path.join(download_dir_base, f'data_downloads_{folder_name}')
        os.makedirs(dest_folder, exist_ok=True)

def run_download():
    """
    Run the download process from last downloaded folder date till latest.
    """
    # Ensure directories exist
    ensure_directories()

    # Determine start date from log
    download_log = load_download_log()
    last_download = download_log.get('last_download')
    if last_download:
        start_date = datetime.fromisoformat(last_download['file_time'])
    else:
        # Default start date if no log exists
        start_date = datetime.strptime("2025-01-01", '%Y-%m-%d')

    end_date = datetime.now()  # Download till latest available

    # Get list of folders from main page
    r_index = session.get(base_url)
    soup = BeautifulSoup(r_index.text, 'html.parser')
    rows = soup.find_all('tr')

    folders_to_download = []
    for row in rows:
        cols = row.find_all('td')
        if len(cols) >= 3:
            link = cols[1].find('a')
            date_str = cols[2].text.strip()
            if link and link['href'].endswith('/'):
                try:
                    folder_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                except ValueError:
                    continue
                if folder_date > start_date:  # Only folders after last downloaded
                    folders_to_download.append((folder_date, link['href']))

    if not folders_to_download:
        return {"status": "error", "message": "No new folders to download"}

    folders_to_download.sort(key=lambda x: x[0])
    last_downloaded_file_info = None
    total_files = 0
    success_files = 0

    for folder_date, foldername in folders_to_download:
        folder_url = f'https://data-argo.ifremer.fr/dac/incois/{foldername}'
        try:
            r_folder = session.get(folder_url)
            r_folder.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(r_folder.text, 'html.parser')
        files_to_download = []
        for row in soup.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) >= 4:
                a_tag = cols[1].find('a', href=True)
                if a_tag:
                    href = a_tag['href']
                    for suffix in file_types.keys():
                        if href.endswith(suffix):
                            files_to_download.append(href)
                            break

        for nc_file in files_to_download:
            folder = file_types.get(next(suf for suf in file_types if nc_file.endswith(suf)), None)
            if not folder:
                continue
            dest_folder = os.path.join(download_dir_base, f'data_downloads_{folder}')
            dest_path = os.path.join(dest_folder, nc_file)

            file_url = f'{folder_url}{nc_file}'
            success = download_file(file_url, dest_path, retries=3)
            total_files += 1
            if success:
                success_files += 1
                # Log folder timestamp (main page timestamp) and last file name
                last_downloaded_file_info = {
                    'folder': foldername,
                    'last_file_name': nc_file,
                    'file_time': folder_date.isoformat()
                }

    # Save log for the last downloaded folder/file
    if last_downloaded_file_info:
        download_log = {'last_download': last_downloaded_file_info}
        save_download_log(download_log)

    return {
        "status": "ok",
        "total_files": total_files,
        "success_files": success_files,
        "last_file": last_downloaded_file_info
    }

if __name__ == "__main__":
    print(run_download())
