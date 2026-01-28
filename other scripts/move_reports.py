import os
import shutil
import configparser

# Load configurations from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Paths from config.ini
HORNS_FOLDER = config['Paths']['horns_folder']
RESULTS_FOLDER = config['Paths']['results_folder']

# Function to collect PNG files from each folder in Horns and move them to the results folder
def collect_png_files(horns_folder, results_folder):
    # Create results folder if it doesn't exist
    if not os.path.exists(results_folder):
        os.makedirs(results_folder)

    # Iterate over each folder in the horns directory
    for folder in os.listdir(horns_folder):
        folder_path = os.path.join(horns_folder, folder)
        
        if not os.path.isdir(folder_path):
            continue

        # Build the path to the expected PNG file
        png_file_path = os.path.join(folder_path, "ABEC_InfiniteBaffle", "Results", f"{folder}.png")
        
        if os.path.exists(png_file_path):
            # Destination path in the results folder
            destination_path = os.path.join(results_folder, f"{folder}.png")
            
            # Move the PNG file to the results folder
            print(f"Moving: {png_file_path} to {destination_path}")
            shutil.move(png_file_path, destination_path)
        else:
            print(f"PNG file not found: {png_file_path}")

# Main execution
if __name__ == "__main__":
    # Collect and move PNG files to the results folder
    collect_png_files(HORNS_FOLDER, RESULTS_FOLDER)
