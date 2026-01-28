import os
import subprocess
import time
import pygetwindow as gw
import pyautogui
import configparser
import tkinter as tk
import keyboard

# Load configurations from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Paths and settings from config.ini
HORNS_FOLDER = config['Paths']['horns_folder']
ABEC_EXE_PATH = config['Paths']['abec_exe_path']
CLEAR_FOLDERS_SCRIPT = os.path.join(os.getcwd(), "clear_folders.py")

# Window settings from config.ini
WINDOW_WIDTH = config.getint('WindowSettings', 'window_width')
WINDOW_HEIGHT = config.getint('WindowSettings', 'window_height')
WINDOW_X = config.getint('WindowSettings', 'window_x')
WINDOW_Y = config.getint('WindowSettings', 'window_y')

# Quicksetup paths
QUICKSETUP_CONFIG = "quicksetup.cfg"
QUICKSETUP_ABEC_FILE = os.path.join(HORNS_FOLDER, "quicksetup", "ABEC_InfiniteBaffle", "Project.abec")

# Run ath.exe with quicksetup.cfg
def run_ath_with_quicksetup():
    command = "ath.exe quicksetup.cfg"
    print(f"Running ATH with command: {command}")
    result = subprocess.run(command, shell=True, capture_output=True, text=True)

    if result.returncode == 0:
        print("ATH executed successfully.")
    else:
        print("Error executing ATH:", result.stderr)
        return False
    return True

# Start ABEC with the generated quicksetup file
def start_abec_with_quicksetup():
    command = f'"{ABEC_EXE_PATH}" "{QUICKSETUP_ABEC_FILE}"'
    print(f"Starting ABEC with command: {command}")
    subprocess.Popen(command, shell=True)

# Wait for the ABEC window by checking for a part of the window title
def wait_for_abec_window(partial_title="ABEC3", timeout=10):
    print(f"Waiting for a window starting with: {partial_title}")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        windows = gw.getAllTitles()
        for title in windows:
            if title.startswith(partial_title):
                print(f"Window found: {title}")
                return gw.getWindowsWithTitle(title)[0]
        time.sleep(0.1)  # check every 0.1 seconds
    
    print(f"Timeout: No window starting with '{partial_title}' found.")
    return None

# Resize, move, and focus the ABEC window by centering the mouse
def set_window_size_position_and_focus(window):
    window.resizeTo(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.moveTo(WINDOW_X, WINDOW_Y)
    print(f"Window resized to {WINDOW_WIDTH}x{WINDOW_HEIGHT} and moved to ({WINDOW_X}, {WINDOW_Y})")

    # Move the mouse to the center of the ABEC window
    center_x = WINDOW_X + WINDOW_WIDTH // 2
    center_y = WINDOW_Y + WINDOW_HEIGHT // 2
    pyautogui.moveTo(center_x, center_y)
    print(f"Mouse moved to the center of the window at ({center_x}, {center_y})")

# Simulate starting the solver by pressing F5 and Enter
def start_solver():
    time.sleep(1)
    print("Starting solver with F5 and Enter...")
    pyautogui.press('f5')
    time.sleep(0.25)
    pyautogui.press('enter')

# Display a tooltip using tkinter near the mouse position
def show_tooltip(text):
    tooltip = tk.Tk()
    tooltip.overrideredirect(True)  # Remove the window decorations
    tooltip.attributes("-topmost", True)  # Keep the tooltip on top
    tooltip.geometry(f"+{pyautogui.position().x + 15}+{pyautogui.position().y + 15}")  # Position near the mouse

    label = tk.Label(tooltip, text=text, background="lightyellow", font=("Arial", 9))
    label.pack()
    
    tooltip.update()  # Display immediately
    return tooltip

# Display tooltip, wait for 's' key, and modify mouse position/color for reference pixel
def capture_and_modify_reference_position_color():
    tooltip = show_tooltip("Move the mouse to a blue pixel on the LEFT edge of the progress bar, then press 's'")
    
    print("Waiting for 's' key to save reference mouse position and color.")
    keyboard.wait('s')
    
    # Capture the current position and pixel color
    x, y = pyautogui.position()
    color = pyautogui.screenshot().getpixel((x, y))
    
    tooltip.destroy()  
    
    # Modify in config.ini
    config.set('PixelCheck', 'ref_pixel_x', str(x))
    config.set('PixelCheck', 'ref_pixel_y', str(y))
    config.set('PixelCheck', 'ref_color', f"{color[0]},{color[1]},{color[2]}")
    with open('config.ini', 'w') as configfile:
        config.write(configfile)
    print(f"Reference position and color saved to config.ini: ({x}, {y}) with color {color}")

# Display tooltip, wait for 's' key, and modify mouse position/color for progress bar
def capture_and_modify_progress_position_color():
    tooltip = show_tooltip("Move the mouse to a blue pixel on the RIGHT edge of the progress bar, then press 's'")
    
    print("Waiting for 's' key to save mouse position and color.")
    keyboard.wait('s')
    
    # Capture the current position and pixel color
    x, y = pyautogui.position()
    color = pyautogui.screenshot().getpixel((x, y))
    
    tooltip.destroy()  # Close the tooltip after capturing the information
    
    # Modify in config.ini
    config.set('PixelCheck', 'pixel_x', str(x))
    config.set('PixelCheck', 'pixel_y', str(y))
    config.set('PixelCheck', 'expected_color', f"{color[0]},{color[1]},{color[2]}")
    with open('config.ini', 'w') as configfile:
        config.write(configfile)
    print(f"Progress bar position and color saved to config.ini: ({x}, {y}) with color {color}")

# Close ABEC by pressing Alt+F4
def close_abec_window():
    abec_window = wait_for_abec_window("ABEC3")
    if abec_window:
        print("Closing the program with Alt + F4...")
        pyautogui.hotkey('alt', 'f4')
        time.sleep(0.25)
        pyautogui.press('enter')

# Run clear_folders.py
def run_clear_folders():
    command = f'python "{CLEAR_FOLDERS_SCRIPT}"'
    print(f"Running clear folders script: {command}")
    subprocess.run(command, shell=True)

# Main function to run the quick setup
def main():
    if run_ath_with_quicksetup():
        start_abec_with_quicksetup()
        abec_window = wait_for_abec_window("ABEC3")
        
        if abec_window:
            set_window_size_position_and_focus(abec_window)
            time.sleep(1)  # Ensure the window is fully loaded
            start_solver()
            
            # Capture and modify reference pixel color for verifying window placement
            capture_and_modify_reference_position_color()
            # Capture and modify progress bar position and color
            capture_and_modify_progress_position_color()
            
            # Close ABEC and run clear folders
            close_abec_window()
            run_clear_folders()
        else:
            print("ABEC window did not open as expected.")
    else:
        print("Failed to execute ATH with quicksetup.cfg.")

if __name__ == "__main__":
    main()
