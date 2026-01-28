import os
import subprocess
import configparser

# Load configurations from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Paths from config.ini
CONFIGS_FOLDER = config['Paths']['configs_folder']
ATH_EXE_PATH = config['Paths']['ath_exe_path']

def run_ath_for_configs(config_dir, ath_exe_path):
    # Get all .cfg files from the config directory
    config_files = [f for f in os.listdir(config_dir) if f.endswith('.cfg')]

    # Execute ath.exe for each config file with the -r option
    for config_file in config_files:
        config_path = os.path.join(config_dir, config_file)
        command = [ath_exe_path, config_path, "-r"]

        # Run the command and capture output
        print(f"Running: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True)

        # Output success or error
        if result.returncode == 0:
            print(f"Success: {config_file}")
            print(result.stdout)
        else:
            print(f"Error running {config_file}: {result.stderr}")

# Main execution
if __name__ == "__main__":
    # Run the report generation for configs in CONFIGS_FOLDER using ATH_EXE_PATH
    run_ath_for_configs(CONFIGS_FOLDER, ATH_EXE_PATH)
