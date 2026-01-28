import os
import subprocess
import time
import pygetwindow as gw
import pyautogui
import math
import configparser
import tkinter as tk

# Load config
config = configparser.ConfigParser()
config.read('config.ini')

# Paths and settings
HORNS_FOLDER = config['Paths']['HORNS_FOLDER']
ABEC_EXE_PATH = config['Paths']['ABEC_EXE_PATH']
WINDOW_WIDTH = config.getint('WindowSettings', 'WINDOW_WIDTH')
WINDOW_HEIGHT = config.getint('WindowSettings', 'WINDOW_HEIGHT')
WINDOW_X = config.getint('WindowSettings', 'WINDOW_X')
WINDOW_Y = config.getint('WindowSettings', 'WINDOW_Y')
PIXEL_X = config.getint('PixelCheck', 'PIXEL_X')
PIXEL_Y = config.getint('PixelCheck', 'PIXEL_Y')
EXPECTED_COLOR = tuple(map(int, config['PixelCheck']['EXPECTED_COLOR'].split(',')))
COLOR_THRESHOLD = config.getint('PixelCheck', 'COLOR_THRESHOLD')
REF_PIXEL_X = config.getint('PixelCheck', 'REF_PIXEL_X')
REF_PIXEL_Y = config.getint('PixelCheck', 'REF_PIXEL_Y')
REF_COLOR = tuple(map(int, config['PixelCheck']['REF_COLOR'].split(',')))
MAX_RETRIES = config.getint('Retries', 'MAX_RETRIES')
TIMEOUT = config.getint('Retries', 'TIMEOUT')

# Euclidean distance to pixel color
def color_distance(c1, c2):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

# Start ABEC with the file
def start_abec_with_file(program_path, file_path):
    command = f'"{program_path}" "{file_path}"'
    print(f"Starting: {command}")
    subprocess.Popen(command, shell=True)

# Wait for window to appear based on title prefix
def wait_for_window(partial_title, timeout=10):
    print(f"Waiting for a window starting with: {partial_title}")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        windows = gw.getAllTitles()
        for title in windows:
            if title.startswith(partial_title):
                print(f"Window found: {title}")
                return gw.getWindowsWithTitle(title)[0]
        time.sleep(0.1)
    
    print(f"Timeout: No window starting with '{partial_title}' found")
    return None

# Close ABEC and make sure it's closed
def wait_for_window_disappearance(partial_title, timeout=10):
    print(f"Waiting for the window with title '{partial_title}' to close...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        windows = gw.getAllTitles()
        if not any(title.startswith(partial_title) for title in windows):
            print(f"Window with title '{partial_title}' has been closed.")
            return True
        time.sleep(0.1)

    # If timeout is reached and the window still exists
    print(f"Timeout: Window with title '{partial_title}' did not close within {timeout} seconds. Retrying to close it...")
    
    # Try to refocus and close the window again
    try:
        abec_window = gw.getWindowsWithTitle(partial_title)[0]
        abec_window.activate()
        time.sleep(1)
        pyautogui.hotkey('alt', 'f4')
        time.sleep(0.5)
        pyautogui.press('enter')
        time.sleep(3)
        
        # Re-check if the window has closed
        if not any(title.startswith(partial_title) for title in gw.getAllTitles()):
            print(f"Window with title '{partial_title}' has been closed successfully.")
            return True
        else:
            print(f"Failed to close the window with title '{partial_title}'.")
            return False
    
    except IndexError:
        print(f"No window with title '{partial_title}' was found to close.")
        return False


# Resize and move the window
def set_window_size_and_position(window):
    window.resizeTo(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.moveTo(WINDOW_X, WINDOW_Y)
    print(f"Window resized to {WINDOW_WIDTH}x{WINDOW_HEIGHT} and moved to ({WINDOW_X}, {WINDOW_Y})")

# Start the solver by pressing F5 followed by Enter
def start_solver():
    time.sleep(1)
    print("Starting solver with F5 and Enter...")
    pyautogui.press('f5')
    time.sleep(0.25)
    pyautogui.press('enter')

# Check if reference pixel color matches expected value to verify positioning
def wait_for_reference_pixel(timeout=TIMEOUT):
    print(f"Waiting for the reference pixel at ({REF_PIXEL_X}, {REF_PIXEL_Y}) to match the expected color: {REF_COLOR}")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        pixel_color = pyautogui.pixel(REF_PIXEL_X, REF_PIXEL_Y)
        distance = color_distance(pixel_color, REF_COLOR)
        print(f"Reference pixel color: {pixel_color}, distance from expected: {distance:.2f}")
        
        if distance <= COLOR_THRESHOLD:
            print("Reference pixel color is close enough to the expected value.")
            return True
        time.sleep(0.25)
    
    print(f"Timeout: Reference pixel color did not match the expected value within {timeout} seconds.")
    return False

# Monitor calculation progress by verifying pixel color at specified coordinates
def monitor_calculation_progress(abec_window_title="ABEC3"):
    print(f"Monitoring calculation progress at ({PIXEL_X}, {PIXEL_Y}) for the expected color: {EXPECTED_COLOR}")
    
    while True:
        # Check if the ABEC window still exists
        if not any(title.startswith(abec_window_title) for title in gw.getAllTitles()):
            print("ABEC window not found. It may have closed unexpectedly.")
            return False  # Stop monitoring if window is gone

        # Check color of the calculation progress pixel
        pixel_color = pyautogui.pixel(PIXEL_X, PIXEL_Y)
        distance = color_distance(pixel_color, EXPECTED_COLOR)
        print(f"Calculation progress pixel color: {pixel_color}, distance from expected: {distance:.2f}")
        
        if distance <= COLOR_THRESHOLD:
            print("Calculation progress pixel color is close enough to the expected value.")
            return True
        time.sleep(0.5)

# Retry logic for solving with up to three timeouts
def solver_with_retry(abec_window):
    retry_step = 0

    while retry_step < 3:
        if wait_for_reference_pixel(TIMEOUT):
            return monitor_calculation_progress()

        retry_step += 1
        if retry_step == 1:
            print("Timeout reached, restarting solver.")
            start_solver()
        elif retry_step == 2:
            print("Timeout reached again, resizing and repositioning window.")
            set_window_size_and_position(abec_window)
            start_solver()
        elif retry_step == 3:
            print("Timeout reached a third time. Quitting ABEC.")
            pyautogui.hotkey('alt', 'f4')
            time.sleep(0.25)
            pyautogui.press('enter')
            wait_for_window_disappearance('ABEC3')  # Ensure ABEC is closed before continuing
            return False
    return False

# Finalize spectra calculation after checking pixel color
def calculate_spectra():
    print("Pressing F7 to calculate spectra...")
    pyautogui.press('f7')

    if monitor_calculation_progress():
        print("Pressing Ctrl + F7 to finalize spectra...")
        pyautogui.hotkey('ctrl', 'f7')
        time.sleep(0.25)
        print("Pressing Enter to confirm the popup...")
        pyautogui.press('enter')
        time.sleep(1)
        print("Closing the program with Alt + F4...")
        pyautogui.hotkey('alt', 'f4')
        time.sleep(0.25)
        pyautogui.press('enter')
        wait_for_window_disappearance('ABEC3')

# Tooltip display function
def show_tooltip(text):
    tooltip = tk.Tk()
    tooltip.overrideredirect(True)
    tooltip.attributes("-topmost", True)
    tooltip.geometry("+10+10")  # Top-left corner of the screen
    label = tk.Label(tooltip, text=text, background="lightyellow", font=("Arial", 10))
    label.pack()
    tooltip.update()  # Display immediately
    return tooltip

def process_abec_folders(base_directory, program_path):
    folders = [f for f in os.listdir(base_directory) if os.path.isdir(os.path.join(base_directory, f))]
    total_files = len(folders)
    current_file = 0

    tooltip = show_tooltip(f"Processing file {current_file} of {total_files}")
    
    for folder in folders:
        current_file += 1
        tooltip_label = f"Processing file {current_file} of {total_files}"
        tooltip.winfo_children()[0].config(text=tooltip_label)  # Update tooltip text
        tooltip.update()

        folder_path = os.path.join(base_directory, folder)
        project_file_path = os.path.join(folder_path, "ABEC_InfiniteBaffle", "Project.abec")
        
        if os.path.exists(project_file_path):
            print(f"Processing: {project_file_path}")
            start_abec_with_file(program_path, project_file_path)
    
            abec_window = wait_for_window('ABEC3')
    
            if abec_window:
                set_window_size_and_position(abec_window)
                time.sleep(1.5)
                start_solver()

                if not solver_with_retry(abec_window):
                    print(f"Failed to complete process for {project_file_path}")
                else:
                    calculate_spectra()
                    print(f"Process completed successfully for {project_file_path}")

            time.sleep(0.5)
        else:
            print(f"Project.abec file not found in {folder_path}")

    # Destroy the tooltip when processing is done
    tooltip.destroy()
# Main
if __name__ == "__main__":
    process_abec_folders(HORNS_FOLDER, ABEC_EXE_PATH)
