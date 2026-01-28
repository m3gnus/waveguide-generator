import os
import numpy as np
import pandas as pd
from scipy.stats import linregress
import configparser

# Load configurations from config.ini
config = configparser.ConfigParser()
config.read('config.ini')
HORNS_FOLDER = config['Paths']['HORNS_FOLDER']

# Define rating parameters with increased differentiation
HIGH_FREQ_RANGE = (5000, 20000)  # Frequency range for primary slope analysis
DEVIATION_TOLERANCE = 0.01  # Lower tolerance to increase sensitivity to deviations
LOW_FREQ_THRESHOLD = 5000  # Minimum frequency for extended match analysis

def rate_waveguide(filepath):
    # Load data from radimp.txt file
    data = pd.read_csv(filepath, sep=" ", header=None, names=["frequency", "re", "img"])
    
    # Filter data for high frequency range for slope analysis
    high_freq_data = data[(data['frequency'] >= HIGH_FREQ_RANGE[0]) & (data['frequency'] <= HIGH_FREQ_RANGE[1])]
    
    # Slope analysis for real component in the high frequency range
    slope, intercept, _, _, _ = linregress(high_freq_data['frequency'], high_freq_data['re'])
    slope_score = max(0, 10 - abs(slope) * 500000)  # Higher sensitivity factor for slope
    
    # Calculate deviation from fitted line in the high frequency range
    fitted_line = intercept + slope * high_freq_data['frequency']
    deviations = np.abs(high_freq_data['re'] - fitted_line)
    deviation_score = max(0, 10 - (np.mean(deviations) / DEVIATION_TOLERANCE) * 4)  # Adjusted scaling for tighter scoring
    
    # Analyze line matching down to lower frequencies
    extended_data = data[data['frequency'] >= LOW_FREQ_THRESHOLD]
    extended_slope, extended_intercept, _, _, _ = linregress(extended_data['frequency'], extended_data['re'])
    match_line = extended_intercept + extended_slope * extended_data['frequency']
    match_deviation = np.abs(extended_data['re'] - match_line)
    match_score = max(0, 10 - (np.mean(match_deviation) / DEVIATION_TOLERANCE) * 4)  # Adjusted scaling for broader frequency conformity

    # Final score calculation as weighted average
    final_score = round((0.5 * slope_score + 0.4 * deviation_score + 0.4 * match_score), 1)
    
    # Print individual scores for debugging
    print(f"{os.path.basename(filepath)} -> Slope Score: {slope_score:.2f}, Deviation Score: {deviation_score:.2f}, Match Score: {match_score:.2f}, Final Score: {final_score}")
    
    return final_score

# Process each radimp file and rate them
def rate_waveguides_in_folder(folder_path):
    ratings = {}
    for folder_name in os.listdir(folder_path):
        results_path = os.path.join(folder_path, folder_name, "ABEC_InfiniteBaffle", "Results")
        for file_name in os.listdir(results_path):
            if file_name.endswith("_radimp.txt"):
                file_path = os.path.join(results_path, file_name)
                rating = rate_waveguide(file_path)
                ratings[file_name] = rating
                
                # Rename files with the rating prefix
                new_name = f"{rating}_{file_name}"
                os.rename(file_path, os.path.join(results_path, new_name))
                png_path = os.path.join(results_path, f"{folder_name}.png")
                if os.path.exists(png_path):
                    os.rename(png_path, os.path.join(results_path, f"{rating}_{folder_name}.png"))
                
                print(f"Rated {file_name}: {rating}")
    return ratings

if __name__ == "__main__":
    ratings = rate_waveguides_in_folder(HORNS_FOLDER)
    for name, rating in ratings.items():
        print(f"{name}: {rating}")
