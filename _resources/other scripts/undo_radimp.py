import os
import configparser
import re

# Load the path from config.ini
config = configparser.ConfigParser()
config.read('config.ini')
horns_folder = config['Paths']['HORNS_FOLDER']

# Regex pattern to identify files with a rating prefix
pattern = re.compile(r"^\d+\.\d+_")  # Matches rating prefix, e.g., '8.5_'

# Loop through each folder in horns_folder
for folder in os.listdir(horns_folder):
    folder_path = os.path.join(horns_folder, folder, "ABEC_InfiniteBaffle", "Results")
    
    # Check if folder exists
    if os.path.exists(folder_path):
        # Process files with the rating prefix in the folder
        for filename in os.listdir(folder_path):
            if pattern.match(filename):
                # Remove the rating prefix from the filename
                new_filename = pattern.sub("", filename)
                old_filepath = os.path.join(folder_path, filename)
                new_filepath = os.path.join(folder_path, new_filename)
                
                # Rename the file
                os.rename(old_filepath, new_filepath)
                print(f"Renamed '{filename}' to '{new_filename}'")

        # Also check the main folder for corresponding PNG files
        main_png_path = os.path.join(horns_folder, folder)
        for filename in os.listdir(main_png_path):
            if filename.endswith('.png') and pattern.match(filename):
                # Remove the rating prefix from the PNG filename
                new_filename = pattern.sub("", filename)
                old_filepath = os.path.join(main_png_path, filename)
                new_filepath = os.path.join(main_png_path, new_filename)
                
                # Rename the file
                os.rename(old_filepath, new_filepath)
                print(f"Renamed '{filename}' to '{new_filename}'")
    else:
        print(f"No 'ABEC_InfiniteBaffle/Results' folder found for '{folder}'")
