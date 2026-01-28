import os
import configparser

# Load the path from config.ini
config = configparser.ConfigParser()
config.read('config.ini')
horns_folder = config['Paths']['HORNS_FOLDER']

# Iterate through each folder in the horns directory
for folder in os.listdir(horns_folder):
    folder_path = os.path.join(horns_folder, folder, "ABEC_InfiniteBaffle", "Results")
    
    # Check if it's a directory and contains a radimp.txt file
    if os.path.isdir(folder_path):
        radimp_file_path = os.path.join(folder_path, "radimp.txt")
        
        if os.path.exists(radimp_file_path):
            # Create the new filename with folder name as prefix
            new_filename = f"{folder}_radimp.txt"
            new_file_path = os.path.join(folder_path, new_filename)
            
            # Rename the radimp.txt file
            os.rename(radimp_file_path, new_file_path)
            print(f"Renamed '{radimp_file_path}' to '{new_file_path}'")
        else:
            print(f"No radimp.txt found in '{folder_path}'")
