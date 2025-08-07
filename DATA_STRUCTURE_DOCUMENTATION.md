# Social Media Data Structure Documentation

## Overview

This document provides a comprehensive description of the data structure used in the Social Media Automation Suite. The system collects, processes, and stores social media data from multiple platforms (LinkedIn, Instagram, Twitter/X, Threads, and Substack) for analysis and performance prediction.

## 🏗️ System Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Social APIs    │────▶│ Data Processing │────▶│    Supabase     │
│   (RapidAPI)    │     │   & Transform   │     │   PostgreSQL    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │     Notion      │
                                                 │   Databases     │
                                                 └─────────────────┘
```

## 📊 Data Flow

1. **Data Collection**: Raw JSON data is fetched from social media APIs via RapidAPI
2. **Data Processing**: JSON data is transformed using field mappings and type conversions
3. **Database Storage**: Processed data is stored in PostgreSQL (Supabase) tables
4. **Data Aggregation**: Raw data is consolidated into summary tables for analysis
5. **Reporting**: Data is synced to Notion for visualization and reporting

## 🗄️ Database Schema

### Raw Data Tables

The system creates individual tables for each platform and data type to store raw API responses:

#### Profile Tables

**linkedin_profile**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('linkedin')
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers

**instagram_profile**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('instagram')
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers

**twitter_profile**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('twitter')
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers

**threads_profile**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('threads')
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers

**substack_profile**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('substack')
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers

#### Posts Tables

**linkedin_posts**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('linkedin')
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes/reactions
- `num_comments` (integer): Number of comments
- `num_reshares` (integer): Number of reshares/reposts

**instagram_posts**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('instagram')
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes
- `num_comments` (integer): Number of comments

**twitter_posts**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('twitter')
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes/favorites
- `num_comments` (integer): Number of comments/replies
- `num_reshares` (integer): Number of reshares/retweets

**threads_posts**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('threads')
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes
- `num_comments` (integer): Number of comments
- `num_reshares` (integer): Number of reshares/reposts

**substack_posts**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier ('substack')
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes/reactions
- `num_comments` (integer): Number of comments
- `num_reshares` (integer): Number of reshares/restacks

### Aggregated Tables

#### Profile Summary Table

**profile**
- `date` (date, PRIMARY KEY): Date of data collection
- `num_followers_linkedin` (integer): LinkedIn follower count
- `num_followers_instagram` (integer): Instagram follower count
- `num_followers_twitter` (integer): Twitter follower count
- `num_followers_substack` (integer): Substack subscriber count
- `num_followers_threads` (integer): Threads follower count

#### Posts Summary Table

**posts**
- `date` (date, PRIMARY KEY): Date of data collection

**Non-Video Posts (by platform):**
- `post_id_linkedin_no_video` (text): LinkedIn non-video post ID
- `posted_at_linkedin_no_video` (date): LinkedIn non-video post date
- `num_likes_linkedin_no_video` (integer): LinkedIn non-video likes
- `num_comments_linkedin_no_video` (integer): LinkedIn non-video comments
- `num_reshares_linkedin_no_video` (integer): LinkedIn non-video reshares

- `post_id_instagram_no_video` (text): Instagram non-video post ID
- `posted_at_instagram_no_video` (date): Instagram non-video post date
- `num_likes_instagram_no_video` (integer): Instagram non-video likes
- `num_comments_instagram_no_video` (integer): Instagram non-video comments

- `post_id_twitter_no_video` (text): Twitter non-video post ID
- `posted_at_twitter_no_video` (date): Twitter non-video post date
- `num_likes_twitter_no_video` (integer): Twitter non-video likes
- `num_comments_twitter_no_video` (integer): Twitter non-video comments
- `num_reshares_twitter_no_video` (integer): Twitter non-video reshares

- `post_id_substack_no_video` (text): Substack non-video post ID
- `posted_at_substack_no_video` (date): Substack non-video post date
- `num_likes_substack_no_video` (integer): Substack non-video likes
- `num_comments_substack_no_video` (integer): Substack non-video comments
- `num_reshares_substack_no_video` (integer): Substack non-video reshares

- `post_id_threads_no_video` (text): Threads non-video post ID
- `posted_at_threads_no_video` (date): Threads non-video post date
- `num_likes_threads_no_video` (integer): Threads non-video likes
- `num_comments_threads_no_video` (integer): Threads non-video comments
- `num_reshares_threads_no_video` (integer): Threads non-video reshares

**Video Posts (by platform):**
- `post_id_linkedin_video` (text): LinkedIn video post ID
- `posted_at_linkedin_video` (date): LinkedIn video post date
- `num_likes_linkedin_video` (integer): LinkedIn video likes
- `num_comments_linkedin_video` (integer): LinkedIn video comments
- `num_reshares_linkedin_video` (integer): LinkedIn video reshares

- `post_id_instagram_video` (text): Instagram video post ID
- `posted_at_instagram_video` (date): Instagram video post date
- `num_likes_instagram_video` (integer): Instagram video likes
- `num_comments_instagram_video` (integer): Instagram video comments

- `post_id_twitter_video` (text): Twitter video post ID
- `posted_at_twitter_video` (date): Twitter video post date
- `num_likes_twitter_video` (integer): Twitter video likes
- `num_comments_twitter_video` (integer): Twitter video comments
- `num_reshares_twitter_video` (integer): Twitter video reshares

- `post_id_substack_video` (text): Substack video post ID
- `posted_at_substack_video` (date): Substack video post date
- `num_likes_substack_video` (integer): Substack video likes
- `num_comments_substack_video` (integer): Substack video comments
- `num_reshares_substack_video` (integer): Substack video reshares

- `post_id_threads_video` (text): Threads video post ID
- `posted_at_threads_video` (date): Threads video post date
- `num_likes_threads_video` (integer): Threads video likes
- `num_comments_threads_video` (integer): Threads video comments
- `num_reshares_threads_video` (integer): Threads video reshares

## 🔗 Data Relationships

### Primary Keys
- All tables use `date` as the primary key
- Raw data tables include composite keys with `platform` and `data_type`

### Foreign Key Relationships
- Aggregated tables (`profile`, `posts`) reference data from raw tables
- Data is joined based on `date` field across all platforms

### Data Consistency
- All dates are stored in 'YYYY-MM-DD' format
- Integer fields use NULL for missing values
- Boolean flags use 1 (true) and 0 (false)

## 📈 Data Collection Process

### 1. API Data Collection
- **Frequency**: Daily collection
- **Platforms**: LinkedIn, Instagram, Twitter/X, Threads, Substack
- **Data Types**: Profile information and recent posts
- **Storage**: Raw JSON files in `results/raw/` directory

### 2. Data Processing
- **Field Mapping**: JSON paths mapped to standardized field names
- **Type Conversion**: Automatic conversion to appropriate data types
- **Validation**: Required fields are validated before processing
- **Error Handling**: Missing or invalid data is logged and handled gracefully

### 3. Database Upload
- **Table Creation**: Tables are created automatically based on DataFrame structure
- **Data Types**: Automatic mapping of pandas types to PostgreSQL types
- **Batch Processing**: Large datasets are processed in batches
- **Upsert Logic**: Existing data is updated, new data is inserted

### 4. Data Aggregation
- **Profile Aggregation**: Daily follower counts from all platforms
- **Posts Aggregation**: Daily post performance metrics separated by content type
- **Consolidation**: One record per day with all platform data

## 🎯 Data Science Applications

### Performance Prediction Features

#### Profile Growth Metrics
- **Daily follower growth rates** by platform
- **Cross-platform follower correlation**
- **Growth acceleration/deceleration patterns**
- **Seasonal growth trends**

#### Content Performance Metrics
- **Engagement rates** (likes, comments, shares per follower)
- **Content type performance** (video vs non-video)
- **Platform-specific performance patterns**
- **Post timing optimization** (day of week, time of day)

#### Predictive Variables
- **Historical engagement patterns**
- **Follower growth trends**
- **Content type distribution**
- **Cross-platform posting frequency**
- **Engagement velocity** (time to peak engagement)

### Machine Learning Opportunities

#### Feature Engineering
```sql
-- Example features for ML models
SELECT 
    date,
    -- Growth features
    num_followers_linkedin - LAG(num_followers_linkedin) OVER (ORDER BY date) as linkedin_growth,
    -- Engagement features
    (num_likes_linkedin_no_video + num_comments_linkedin_no_video + num_reshares_linkedin_no_video) / 
        NULLIF(num_followers_linkedin, 0) as linkedin_engagement_rate,
    -- Cross-platform features
    num_followers_linkedin + num_followers_instagram + num_followers_twitter as total_followers
FROM profile p
LEFT JOIN posts ps ON p.date = ps.date
```

#### Target Variables
- **Next post engagement prediction**
- **Optimal posting time prediction**
- **Content type recommendation**
- **Platform performance forecasting**

## 📊 Data Quality Considerations

### Data Completeness
- **Missing Data**: Some platforms may not return data on certain days
- **API Limitations**: Rate limits may prevent complete data collection
- **Platform Changes**: API structure changes may affect data collection

### Data Validation
- **Range Checks**: Engagement metrics should be non-negative
- **Consistency Checks**: Follower counts should generally increase over time
- **Cross-Platform Validation**: Similar content should perform similarly across platforms

### Data Cleaning
- **Outlier Detection**: Unusually high engagement may indicate viral content
- **Duplicate Removal**: Same post may appear multiple times
- **Date Normalization**: All dates converted to consistent format

## 🔧 Technical Implementation

### Database Configuration
- **Environment**: Supabase (PostgreSQL)
- **Connection**: Environment-specific configuration
- **Authentication**: API key-based access
- **Backup**: Automatic daily backups

### Data Processing Pipeline
1. **Collection**: `social_api_client.py`
2. **Processing**: `data_processor.py`
3. **Upload**: `supabase_uploader.py`
4. **Aggregation**: `profile_aggregator.py`, `posts_consolidator.py`

### Configuration Files
- **API Configuration**: `config/config.json`
- **Field Mapping**: `config/mapping.json`
- **Database Settings**: Environment variables

## 📈 Analytics Queries

### Growth Analysis
```sql
-- Monthly follower growth by platform
SELECT 
    DATE_TRUNC('month', date) as month,
    platform,
    AVG(num_followers) as avg_followers,
    MAX(num_followers) - MIN(num_followers) as growth
FROM (
    SELECT date, 'linkedin' as platform, num_followers_linkedin as num_followers FROM profile
    UNION ALL
    SELECT date, 'instagram' as platform, num_followers_instagram as num_followers FROM profile
    UNION ALL
    SELECT date, 'twitter' as platform, num_followers_twitter as num_followers FROM profile
) combined
GROUP BY month, platform
ORDER BY month, platform;
```

### Engagement Analysis
```sql
-- Average engagement by content type and platform
SELECT 
    'linkedin' as platform,
    'video' as content_type,
    AVG(num_likes_linkedin_video) as avg_likes,
    AVG(num_comments_linkedin_video) as avg_comments,
    AVG(num_reshares_linkedin_video) as avg_reshares
FROM posts
WHERE num_likes_linkedin_video IS NOT NULL
UNION ALL
SELECT 
    'linkedin' as platform,
    'non-video' as content_type,
    AVG(num_likes_linkedin_no_video) as avg_likes,
    AVG(num_comments_linkedin_no_video) as avg_comments,
    AVG(num_reshares_linkedin_no_video) as avg_reshares
FROM posts
WHERE num_likes_linkedin_no_video IS NOT NULL;
```

### Performance Prediction Features
```sql
-- Features for ML model training
SELECT 
    p.date,
    -- Historical performance (7-day average)
    AVG(ps.num_likes_linkedin_no_video) OVER (
        ORDER BY p.date ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
    ) as avg_likes_7d,
    -- Growth momentum
    p.num_followers_linkedin - LAG(p.num_followers_linkedin, 7) OVER (ORDER BY p.date) as follower_growth_7d,
    -- Engagement rate
    CASE 
        WHEN p.num_followers_linkedin > 0 
        THEN (ps.num_likes_linkedin_no_video + ps.num_comments_linkedin_no_video) / p.num_followers_linkedin
        ELSE NULL 
    END as engagement_rate
FROM profile p
LEFT JOIN posts ps ON p.date = ps.date
WHERE ps.num_likes_linkedin_no_video IS NOT NULL;
```

## 🚀 Future Enhancements

### Additional Data Sources
- **YouTube**: Video performance metrics
- **TikTok**: Short-form video engagement
- **Newsletter**: Email open rates and click-through rates
- **Website Analytics**: Traffic from social media

### Advanced Analytics
- **Sentiment Analysis**: Content sentiment correlation with engagement
- **Topic Modeling**: Content themes and performance patterns
- **Network Analysis**: Influencer interactions and mentions
- **A/B Testing**: Content format performance comparison

### Machine Learning Models
- **Time Series Forecasting**: Engagement prediction models
- **Classification Models**: Content type recommendation
- **Clustering**: Audience segmentation
- **Recommendation Systems**: Optimal posting strategies

## 📝 Conclusion

This data structure provides a comprehensive foundation for social media performance analysis and prediction. The normalized schema, consistent data types, and aggregated views enable efficient querying and analysis for data science applications. The system's modular design allows for easy extension with additional platforms and metrics.

For data science projects focused on performance prediction, the key tables to focus on are:
1. **`profile`** - For follower growth analysis and audience insights
2. **`posts`** - For content performance analysis and engagement prediction
3. **Raw tables** - For detailed post-level analysis and feature engineering

The data is structured to support both descriptive analytics (what happened) and predictive analytics (what will happen), making it ideal for building machine learning models to optimize social media strategy.