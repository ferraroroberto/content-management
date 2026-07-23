# Data schema

The Supabase (PostgreSQL) schema behind the reporting pipeline: raw per-platform
tables, the consolidated daily aggregates, and the Notion sync tables. Moved out
of `README.md`'s orientation map into its own reference file (issue #170) — the
README links here rather than carrying the column-level detail.

## Database architecture

The system uses PostgreSQL (via Supabase) with a normalized schema that separates raw data collection from aggregated analytics. All tables use `date` as the primary key for efficient time-series queries.

## Raw data tables

The system creates individual tables for each platform and data type to store raw API responses:

### Profile tables
- **`linkedin_profile`**: LinkedIn follower counts and profile data
- **`instagram_profile`**: Instagram follower counts and profile data
- **`twitter_profile`**: Twitter/X follower counts and profile data
- **`threads_profile`**: Threads follower counts and profile data
- **`substack_profile`**: Substack subscriber counts and profile data

**Common profile fields:**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers/subscribers

### Posts tables
- **`linkedin_posts`**: LinkedIn post performance metrics
- **`instagram_posts`**: Instagram post performance metrics
- **`twitter_posts`**: Twitter/X post performance metrics
- **`threads_posts`**: Threads post performance metrics
- **`substack_posts`**: Substack post performance metrics

**Common posts fields:**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes/reactions
- `num_comments` (integer): Number of comments
- `num_reshares` (integer): Number of reshares/reposts

## Aggregated tables

### Profile summary table
**`profile`** - Consolidated daily follower counts across all platforms:
- `date` (date, PRIMARY KEY): Date of data collection
- `num_followers_linkedin` (integer): LinkedIn follower count
- `num_followers_instagram` (integer): Instagram follower count
- `num_followers_twitter` (integer): Twitter follower count
- `num_followers_substack` (integer): Substack subscriber count
- `num_followers_threads` (integer): Threads follower count

### Posts summary table
**`posts`** - Daily post performance metrics separated by content type:
- `date` (date, PRIMARY KEY): Date of data collection

**Non-video posts (by platform):**
- `post_id_*_no_video`: Post ID for latest non-video content
- `posted_at_*_no_video`: Publication date
- `num_likes_*_no_video`: Engagement metrics
- `num_comments_*_no_video`: Comment counts
- `num_reshares_*_no_video`: Share counts

**Video posts (by platform):**
- `post_id_*_video`: Post ID for latest video content
- `posted_at_*_video`: Publication date
- `num_likes_*_video`: Engagement metrics
- `num_comments_*_video`: Comment counts
- `num_reshares_*_video`: Share counts

*(* = linkedin, instagram, twitter, substack, threads)

## Notion database integration

### Two-stage data pipeline
- **Stage 1 (raw ingestion):** platform-specific tables store raw API responses; Notion sync uses dynamic schema detection with bidirectional sync and change tracking; complex data types are stored as JSONB.
- **Stage 2 (consolidation):** SQL aggregation scripts merge platform-specific raw data into the unified `profile` / `posts` tables above, optimized for time-series analysis and cross-platform comparisons.

### Common Notion table structure
All Notion-synced tables share standardized columns:

| Column | Data Type | Description |
| :--- | :--- | :--- |
| `notion_id` | `text` | Notion page ID (UUID) - **Primary Key** |
| `created_time` | `timestamp with time zone` | When page was created in Notion |
| `last_edited_time` | `timestamp with time zone` | When page was last edited |
| `archived` | `boolean` | Whether page is archived |
| `notion_data_jsonb` | `jsonb` | Complex data types and unmapped properties |

### Dynamic schema generation
Tables are automatically created with columns derived from Notion properties:
- **Property names** → normalized column names (lowercase, underscores)
- **Data types** automatically mapped from Notion to PostgreSQL
- **Complex types** (relations, arrays) stored in JSONB column

### Notion to PostgreSQL type mapping

| Notion Property Type | PostgreSQL Data Type |
| :--- | :--- |
| Title, Rich Text, URL, Email, Phone | `text` |
| Number | `bigint` or `double precision` |
| Select, Status | `text` |
| Date | `timestamp with time zone` |
| Checkbox | `boolean` |
| Formula (various) | Mapped to appropriate types |
| Multi-Select, Relation, People, Files | `jsonb` |

### Integrated Notion databases

The system syncs data from **15+ Notion databases** for comprehensive content management:

**Content & Publishing:**
- `notion_posts` - Social media posts and content
- `notion_articles` - Blog articles and written content
- `notion_newsletter` - Newsletter content and campaigns

**Media & Assets:**
- `notion_clips` - Video/audio clips and media assets
- `notion_illustrations` - Images and visual content
- `notion_visual_types` - Media categorization

**Business & Analytics:**
- `notion_companies` - Company profiles and relationships
- `notion_connections` - Network and relationship data
- `notion_interactions` - User engagement and interactions

**Content Strategy:**
- `notion_editorial` - Editorial calendar and planning
- `notion_concepts` - Content ideas and brainstorming
- `notion_books` - Book recommendations and reviews
- `notion_books_recommendations` - Reading lists and suggestions

**Additional databases:**
- `notion_episodes` - Podcast episodes and series
- `notion_comments` - User comments and feedback
- `notion_wins_and_features` - Success metrics and feature tracking

### Database relationships & constraints

**Social media data:**
- Raw platform tables feed into consolidated tables
- Foreign key relationships based on `date` field
- No traditional foreign keys between Notion tables

**Notion data:**
- Relationships stored as Notion page ID arrays in JSONB
- Application-layer joins required for complex queries
- Preserves Notion's flexible relationship model
