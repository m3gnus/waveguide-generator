import os
import numpy as np
import configparser

# Initialize configuration readers
config = configparser.ConfigParser()
params = configparser.ConfigParser()

# Load configurations from config.ini and params.ini
config.read('config.ini')
params.read('params.ini')

# Access paths from config.ini
CONFIGS_FOLDER = config['Paths']['CONFIGS_FOLDER']

# Define parameter ranges using params.ini or default values if None
def get_range_or_default(param_name):
    range_value = params['Params'].get(param_name, "None").split('#')[0].strip()
    
    if range_value == "None":
        default_param_name = param_name.replace('_range', '')
        return [float(params['Defaults'][default_param_name])] if default_param_name in params['Defaults'] else []
    start, stop, steps = map(str.strip, range_value.split(','))
    return np.linspace(float(start), float(stop), int(steps))

# Retrieve parameter ranges or default values
r0_values = get_range_or_default('r0_range')
a0_values = get_range_or_default('a0_range')
a_values = get_range_or_default('a_range')
k_values = get_range_or_default('k_range')
L_values = get_range_or_default('L_range')
s_values = get_range_or_default('s_range')
n_values = get_range_or_default('n_range')
q_values = get_range_or_default('q_range')

# Load the waveguide size limits from params.ini
min_size = params.getfloat('WaveguideSize', 'min_size')
max_size = params.getfloat('WaveguideSize', 'max_size')

# Load the base config template from base_template.txt
with open('base_template.txt', 'r') as file:
    base_config_content = file.read()

# Define the waveguide size calculation function
def calculate_y_at_L(a0_deg, a_deg, r0, k, L, s, n, q):
    # Convert angles to radians
    a0 = np.radians(a0_deg)
    a = np.radians(a_deg)
    x = L
    term1 = np.sqrt((k * r0) ** 2 + 2 * k * r0 * x * np.tan(a0) + (x * np.tan(a)) ** 2)
    term2 = r0 * (1 - k)
    term3 = (L * s / q) * (1 - (1 - (q * x / L) ** n) ** (1 / n))
    return term1 + term2 + term3

# Function to generate config content with updated parameters
def generate_config_content(r0, a0, a, k, L, s, n, q):
    return base_config_content.format(r0=r0, a0=a0, a=a, k=k, L=L, s=s, n=n, q=q)

# Main script to generate config files with different parameter combinations
def generate_configs(output_directory):
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    created_count = 0
    skipped_count = 0

    for r0 in r0_values:
        for a0 in a0_values:
            for a in a_values:
                for k in k_values:
                    for L in L_values:
                        for s in s_values:
                            for n in n_values:
                                for q in q_values:
                                    # Calculate waveguide size and skip config if out of bounds
                                    y_at_L = calculate_y_at_L(a0, a, r0, k, L, s, n, q)
                                    if not (min_size <= y_at_L <= max_size):
                                        print(f"Skipped config (r0={r0}, a0={a0}, a={a}, k={k}, L={L}, s={s}, n={n}, q={q}) - y_at_L={y_at_L} out of bounds.")
                                        skipped_count += 1
                                        continue

                                    # Generate config content if within bounds
                                    config_content = generate_config_content(r0, a0, a, k, L, s, n, q)
                                    filename = f"L-{L:.2f}_a-{a:.2f}_r0-{r0:.2f}_a0-{a0:.2f}_k-{k:.2f}_s-{s:.2f}_q-{q:.3f}_n-{n:.2f}.cfg"
                                    filepath = os.path.join(output_directory, filename)

                                    with open(filepath, 'w') as config_file:
                                        config_file.write(config_content)
                                    print(f"Created: {filename}")
                                    created_count += 1


    print(f"\nTotal configurations created: {created_count}")
    print(f"Total configurations skipped: {skipped_count}")

if __name__ == "__main__":
    generate_configs(CONFIGS_FOLDER)
