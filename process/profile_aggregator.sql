-- Create the table if it doesn't exist
CREATE TABLE IF NOT EXISTS profile (
    date date PRIMARY KEY,
    num_followers_linkedin integer,
    num_followers_instagram integer,
    num_followers_twitter integer,
    num_followers_substack integer,
    num_followers_threads integer
);

-- Clear existing data while preserving table structure and relationships
TRUNCATE TABLE profile;

-- Insert new data
INSERT INTO profile
WITH base_dates AS (
    SELECT DISTINCT date 
    FROM (
        SELECT date FROM linkedin_profile
        UNION
        SELECT date FROM instagram_profile
        UNION
        SELECT date FROM twitter_profile
        UNION
        SELECT date FROM substack_profile
        UNION
        SELECT date FROM threads_profile
    ) as all_dates
)

SELECT 
    base_dates.date
    , linkedin_profile.num_followers as num_followers_linkedin
    , instagram_profile.num_followers as num_followers_instagram
    , twitter_profile.num_followers as num_followers_twitter
    , substack_profile.num_followers as num_followers_substack
    , threads_profile.num_followers as num_followers_threads

FROM base_dates

LEFT JOIN linkedin_profile 
ON base_dates.date = linkedin_profile.date 
AND linkedin_profile.platform = 'linkedin' 
AND linkedin_profile.data_type = 'profile'

LEFT JOIN instagram_profile 
ON base_dates.date = instagram_profile.date 
AND instagram_profile.platform = 'instagram' 
AND instagram_profile.data_type = 'profile'

LEFT JOIN twitter_profile 
ON base_dates.date = twitter_profile.date 
AND twitter_profile.platform = 'twitter' 
AND twitter_profile.data_type = 'profile'

LEFT JOIN substack_profile 
ON base_dates.date = substack_profile.date 
AND substack_profile.platform = 'substack' 
AND substack_profile.data_type = 'profile'

LEFT JOIN threads_profile 
ON base_dates.date = threads_profile.date 
AND threads_profile.platform = 'threads' 
AND threads_profile.data_type = 'profile'

ORDER BY base_dates.date;

-- Add table comment
COMMENT ON TABLE profile IS 'Consolidated profile data from all platforms with follower counts';
