import subprocess
import os

# List of scripts to be executed in order
scripts_to_run = [

    "clear_folders.py",
    "generate_configs_linspace.py",
    "generate_abec_files.py",
    "run_abec_fast.py",
    "generate_reports.py",
    "move_reports.py",
    "clear_folders.py",
    "rate_radimp.py",
]

# Function to run each script
def run_script(script_name):
    try:
        print(f"Running: {script_name}")
        # Run the script and wait for it to complete
        subprocess.run(["python", script_name], check=True)
        print(f"Finished: {script_name}\n")
    except subprocess.CalledProcessError as e:
        print(f"Error running {script_name}: {e}")
        exit(1)  # Exit if there's an error

# Main execution
if __name__ == "__main__":
    # Check if the scripts exist in the current directory
    current_directory = os.getcwd()
    
    for script in scripts_to_run:
        script_path = os.path.join(current_directory, script)
        
        if os.path.exists(script_path):
            run_script(script)
        else:
            print(f"Script not found: {script}")
            exit(1)  # Exit if a script is missing
