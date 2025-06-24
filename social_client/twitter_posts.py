import requests
import json
from datetime import datetime
import os

def load_config():
    """Load configuration from config.json file."""
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
    config_path = os.path.join(config_dir, 'config.json')
    
    try:
        with open(config_path, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        print(f"Error: Configuration file not found at {config_path}")
        return None
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON in configuration file at {config_path}")
        return None

def get_twitter_posts(config):
    """Fetch Twitter posts data using the API."""
    if not config or 'twitter_posts' not in config:
        print("Twitter posts configuration not found in config file")
        return None
    
    twitter_config = config['twitter_posts']
    
    url = twitter_config.get('api_url')
    headers = {
        "x-rapidapi-key": twitter_config.get('api_key'),
        "x-rapidapi-host": twitter_config.get('api_host')
    }
    querystring = twitter_config.get('querystring', {})
    
    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()  # Raise an exception for bad status codes
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Twitter posts: {e}")
        return None

def save_results(data):
    """Save the results to a JSON file in the results directory."""
    if not data:
        print("No data to save")
        return
    
    # Create results directory if it doesn't exist
    results_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'results')
    os.makedirs(results_dir, exist_ok=True)
    
    # Add metadata
    current_date = datetime.now().strftime('%Y-%m-%d')
    result_data = {
        "date": current_date,
        "platform": "Twitter",
        "data_type": "posts",
        "data": data
    }
    
    # Generate filename with date
    filename = f"twitter_posts_{current_date}.json"
    file_path = os.path.join(results_dir, filename)
    
    # Check if file already exists and delete it
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"Deleted existing file: {file_path}")
    
    # Save to file
    with open(file_path, 'w', encoding='utf-8') as file:
        json.dump(result_data, file, indent=4, ensure_ascii=False)
    
    print(f"Results saved to {file_path}")

def main():
    """Main function to execute the Twitter posts retrieval."""
    # Load configuration
    config = load_config()
    if not config:
        return
    
    # Get Twitter posts data
    posts_data = get_twitter_posts(config)
    if not posts_data:
        return
    
    # Save results
    save_results(posts_data)

if __name__ == "__main__":
    main()
