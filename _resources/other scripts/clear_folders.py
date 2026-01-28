import os
import shutil
import configparser

# Load configurations from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Access paths from config.ini
HORNS_FOLDER = config['Paths']['HORNS_FOLDER']
CONFIGS_FOLDER = config['Paths']['CONFIGS_FOLDER']

# Function to clear out a folder
def clear_folder(folder_path):
    if os.path.exists(folder_path):
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)  # Remove directory and all its contents
            else:
                os.remove(item_path)  # Remove file
        print(f"Cleared folder: {folder_path}")
    else:
        print(f"Folder does not exist: {folder_path}")

# Main execution
if __name__ == "__main__":
    # Clear out the Horns folder from config.ini
    clear_folder(HORNS_FOLDER)

    # Clear out the Configs folder from config.ini
    clear_folder(CONFIGS_FOLDER)
