# Social Media Data Scraper

This tool fetches LinkedIn, Instagram, Twitter, Threads, and Substack profile information and posts using APIs and saves the results to JSON files.

## Setup

1. Make sure you have Python installed
2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```
3. Configuration is stored in `config/config.json`

## Usage

### Collect All Data at Once

Run the data collection script from the `reporting` directory:

```powershell
# To collect data from all social media platforms
python collect_data.py

# To run in debug mode with more detailed logging
python collect_data.py --debug
```

### Run Individual Modules

You can also run individual modules from the `reporting` directory:

```powershell
# To fetch LinkedIn profile data
python social_client/linkedin_profile.py

# To fetch LinkedIn posts
python social_client/linkedin_posts.py

# To fetch Instagram profile data
python social_client/instagram_profile.py

# To fetch Instagram posts
python social_client/instagram_posts.py

# To fetch Twitter profile data
python social_client/twitter_profile.py

# To fetch Twitter posts
python social_client/twitter_posts.py

# To fetch Threads profile data
python social_client/threads_profile.py

# To fetch Threads posts
python social_client/threads_posts.py

# To fetch Substack profile data
python social_client/substack_profile.py

# To fetch Substack posts
python social_client/substack_posts.py
```

## Logging

The application uses a comprehensive logging system:

- Console logs: All logs are displayed in the console
- File logs: 
  - Individual module logs are saved in the `logs` directory with the module name
  - The main collection script logs are saved as `collect_data_YYYY-MM-DD.log`
- Log levels:
  - INFO: Normal operation logs (default)
  - DEBUG: Detailed debugging information (use `--debug` flag)
- Emojis are used in logs for better visual identification of log types

## Results

The scripts will save the results to the `results` directory in JSON files with the following formats:

### LinkedIn Profile Data
- Filename: `linkedin_profile_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "LinkedIn"
  - data_type: "profile"
  - data: The full LinkedIn profile data

### LinkedIn Posts Data
- Filename: `linkedin_posts_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "LinkedIn"
  - data_type: "posts"
  - data: The LinkedIn posts data

### Instagram Profile Data
- Filename: `instagram_profile_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Instagram"
  - data_type: "profile"
  - data: The Instagram profile data

### Instagram Posts Data
- Filename: `instagram_posts_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Instagram"
  - data_type: "posts"
  - data: The Instagram posts data

### Twitter Profile Data
- Filename: `twitter_profile_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Twitter"
  - data_type: "profile"
  - data: The Twitter profile data

### Twitter Posts Data
- Filename: `twitter_posts_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Twitter"
  - data_type: "posts"
  - data: The Twitter posts data

### Threads Profile Data
- Filename: `threads_profile_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Threads"
  - data_type: "profile"
  - data: The Threads profile data

### Threads Posts Data
- Filename: `threads_posts_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Threads"
  - data_type: "posts"
  - data: The Threads posts data

### Substack Profile Data
- Filename: `substack_profile_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Substack"
  - data_type: "profile"
  - data: The Substack profile data

### Substack Posts Data
- Filename: `substack_posts_YYYY-MM-DD.json`
- Content includes:
  - date: Current date
  - platform: "Substack"
  - data_type: "posts"
  - data: The Substack posts data

## Customization

To change the profile URLs, usernames, or other API settings, edit the `config/config.json` file.

## Note

If a result file for the current date already exists, it will be deleted and overwritten with new data.
