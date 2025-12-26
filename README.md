# SofaScore Scraper

A Python application designed to fetch, analyze, and manage football match data from SofaScore. It provides comprehensive capabilities for collecting, processing, and exporting data about various leagues, seasons, and matches.

<div align="center">
    
![SofaScore Scraper](https://img.shields.io/badge/SofaScore-Scraper-blue)
![Python](https://img.shields.io/badge/Python-3.8%2B-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)
    
</div>

For Turkish documentation, please see [README.tr.md](README.tr.md).

## 📋 Table of Contents

- [Features](#features)
- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Main Menu](#main-menu)
  - [League Management](#league-management)
  - [Season Data](#season-data)
  - [Schedule & Results](#schedule--results)
  - [Detailed Match Stats](#detailed-match-stats)
- [Configuration](#configuration)
- [Data Structure](#data-structure)
- [Outputs and Data Formats](#outputs-and-data-formats)
- [How-to](#how-to)
  - [Adding a New League](#adding-a-new-league)
  - [Fetching All Matches for a Season](#fetching-all-matches-for-a-season)
  - [Creating CSV Datasets](#creating-csv-datasets)
  - [Analyzing Match Details](#analyzing-match-details)
  - [Exporting for Data Analysis](#exporting-for-data-analysis)
- [FAQ](#faq)
- [Architecture and Development](#architecture-and-development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## ✨ Features

SofaScore Scraper offers the following key features:

- **League Management**:
  - List, add, and remove leagues
  - View all supported SofaScore leagues
  - **New:** Search for leagues by name

- **Season Operations**:
  - Fetch and list all seasons for leagues
  - Automatically detect active seasons
  - Manage past and future seasons

- **Match Data**:
  - Fetch match lists for specific leagues and seasons
  - View weekly/round match data
  - Collect bulk match data for all leagues

- **Detailed Match Stats**:
  - Fetch rich match statistics
  - View team streaks and forms
  - Collect pre-game form data
  - Analyze Head-to-Head (H2H) statistics

- **Data Export**:
  - Convert match data to CSV format
  - Export data by league or in bulk
  - Save single match details as CSV

- **Multi-language Support**:
  - Turkish and English language options
  - Instant language switching within the app

- **User Interface**:
  - Intuitive terminal-based menu system
  - Colorful and categorized outputs
  - Progress bars and status indicators
  - Detailed error messages and logging

## 💻 System Requirements

- Python 3.8 or higher
- pip (Python package manager)
- Internet connection (to access SofaScore API)
- 100 MB+ disk space (varies based on collected data)

## 🔧 Installation

### 1. Clone the Project

Clone the project from the GitHub repository:

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
```

Alternatively, download and extract the ZIP file.

### 2. Create Virtual Environment (Optional but Recommended)

Creating a virtual environment helps avoid package conflicts:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# For Linux/MacOS:
source venv/bin/activate
# For Windows:
venv\Scripts\activate
```

### 3. Install Dependencies

Install the required Python packages:

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

You can configure environment variables using the `.env` file:

```bash
# Copy the example file
cp .env.example .env
# Edit the file
nano .env  # or your preferred text editor
```

## 📘 Usage

Run the application using the following command in the main directory:

```bash
python main.py
```

To run with specific parameters:

```bash
# Run headless (without UI)
python main.py --headless

# Update data for all leagues
python main.py --headless --update-all

# Export data to CSV
python main.py --headless --csv-export
```

### Main Menu

When the application starts, you will see the main menu:

```
SofaScore Scraper v1.0.0
==========================================

Main Menu:
--------------------------------------------------
1. 🏆 League Management
2. 📅 Season Data
3. 📅 Schedule & Results
4. 📊 Detailed Match Stats
5. 📊 Statistics
6. ⚙️ Settings
0. ❌ Exit

Your choice (0-6): 
```

### League Management

1. **List Leagues**: View configured leagues
2. **Add New League**: Add a new league (Name and ID required)
3. **Reload League Config**: Reload changes from the league file
4. **Search League**: Search leagues by name (New!)
0. **Back to Main Menu**: Return to the main menu

### Season Data

1. **List Seasons**: View stored seasons
2. **Update Seasons for Single League**: Fetch season data for a specific league
3. **Update Seasons for All Leagues**: Fetch season data for all leagues
0. **Back to Main Menu**: Return to the main menu

### Schedule & Results

1. **List Fetched Matches**: List downloaded matches
2. **Fetch Matches for Single League**: Fetch match data for a specific league and season
3. **Fetch Matches for All Leagues**: Fetch match data for all leagues
0. **Back to Main Menu**: Return to the main menu

### Detailed Match Stats

1. **Fetch Details for Specific Matches**: Fetch detailed data for selected matches
2. **Fetch Details for All Matches**: Fetch detailed data for all matches
3. **Generate CSV Dataset**: Convert match data to CSV format
0. **Back to Main Menu**: Return to the main menu

## ⚙️ Configuration

SofaScore Scraper uses two main configuration methods: environment variables and league configuration.

### 1. .env File

The project is configured via the `.env` file. The application automatically creates one if it doesn't exist. You can update these settings easily from the Settings menu.

Example `.env` file:

```
# API Configuration
API_BASE_URL=https://www.sofascore.com/api/v1
REQUEST_TIMEOUT=20
MAX_RETRIES=3
MAX_CONCURRENT=25
WAIT_TIME_MIN=0.4
WAIT_TIME_MAX=0.8
USE_PROXY=false
PROXY_URL=

# Data Configuration
DATA_DIR=data
FETCH_ONLY_FINISHED=true
SAVE_EMPTY_ROUNDS=false

# Display Settings
USE_COLOR=true
DATE_FORMAT=%Y-%m-%d %H:%M:%S

# Debugging
LOG_LEVEL=INFO
DEBUG=false

# Language Setting
LANGUAGE=en
```

You can change the following configurations from the Settings menu:
- API Configuration (URL, timeout, retries, etc.)
- Data Directory (storage location)
- Display Settings (color usage, date format)
- Language Selection (Turkish / English)
- Backup and Restore operations

### 2. League Configuration

You can manage leagues in the `config/leagues.txt` file. This determines which leagues the application tracks:

```
# Format: League Name: ID
Premier League: 17
LaLiga: 8
Serie A: 23
Bundesliga: 35
Ligue 1: 34
Süper Lig: 52
```

You can add, edit, or remove leagues from the League Management menu.

## 📂 Data Structure

SofaScore Scraper organizes collected data in the following structure:

```
data/
├── seasons/
│   └── {league_id}_{league_name}_seasons.json
├── matches/
│   └── {league_id}_{league_name}/
│       └── {season_id}_{season_name}/
│           ├── round_1.json
│           ├── round_2.json
│           └── ...
└── match_details/
    └── {league_name}/
        └── season_{season_name}/
            └── {match_id}/
                ├── basic.json
                ├── statistics.json
                ├── team_streaks.json
                ├── pregame_form.json
                ├── h2h.json
                └── lineups.json
```

### Data Files

1. **seasons.json**: List of all seasons for a league
2. **round_X.json**: Matches for a specific round/week
3. **basic.json**: Basic match info (teams, score, date, etc.)
4. **statistics.json**: Match statistics (shots, passes, corners, etc.)
5. **team_streaks.json**: Team streaks/stats
6. **pregame_form.json**: Pre-game team form
7. **h2h.json**: Head-to-Head history
8. **lineups.json**: Lineups and player info

## 📊 Outputs and Data Formats

### CSV Outputs

CSV files are saved in `data/match_details/processed/`:

1. **Single Match CSV**: `{match_id}_{timestamp}.csv`
2. **League Matches CSV**: `{league_name}_{timestamp}.csv`
3. **All Matches CSV**: `all_matches_{timestamp}.csv`

Example CSV output:

```csv
match_id,tournament_name,season_name,round,home_team_name,away_team_name,home_score_ft,away_score_ft,match_date,home_possession,away_possession,home_shots_total,away_shots_total,home_shots_on_target,away_shots_on_target
10257123,Premier League,2023/2024,38,Manchester City,West Ham,3,1,1621789200,65,35,23,5,12,2
```

## 🛠 How-to

### Adding a New League

There are two ways to add a new league:

#### 1. Via Application:
Select "League Management" from the main menu and use the "Add New League" option.

#### 2. Directly via `leagues.txt`:

1. Open `config/leagues.txt` in a text editor.
2. Add the new league in the format: `League Name: ID`

```
Premier League: 17
LaLiga: 8
```

### Fetching All Matches for a Season

To fetch all matches for a specific league and season:

1. Select "Schedule & Results" (3) from the main menu.
2. Select "Fetch Matches for Single League" (2).
3. Select the target league from the list.
4. Choose "Specific Season" (3) from the filter options.
5. Select the desired season from the list.

### Creating CSV Datasets

To convert match data to CSV format:

1. Select "Detailed Match Stats" (4) from the main menu.
2. Select "Generate CSV Dataset" (3).
3. Choose one of the conversion options:
   - Single Match CSV
   - League CSV
   - All Leagues CSV

## ❓ FAQ

### 1. How do I find a League ID?

You can find the ID in the URL on the SofaScore website. For example, for Premier League, the URL is `https://www.sofascore.com/tournament/football/england/premier-league/17`. The last number (17) is the League ID.

### 2. How do I find a Match ID?

You can find Match IDs by:
- Using the "List Fetched Matches" option in the app
- Checking the fetched JSON files

### 3. I'm getting rate-limiting errors. What should I do?

If you make too many requests, you might hit rate limits. Try:
- Lowering `MAX_CONCURRENT` in `.env` (e.g., to 10)
- Increasing `WAIT_TIME_MIN` and `WAIT_TIME_MAX`
- Fetching data in smaller batches

### 4. Can I run this in another language?

Yes! The application supports both English and Turkish. You can change the language from the Settings menu.

## 🏗 Architecture

The project uses a modular architecture:

1. **ConfigManager**: Configuration and env variable management
2. **SeasonFetcher**: Fetching and managing seasons
3. **MatchFetcher**: Fetching match lists
4. **MatchDataFetcher**: Fetching detailed match stats
5. **UI Modules**: Separate handlers for different menu sections

## 🤝 Contributing

1. Fork the repository
2. Create a new branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

Developer: [Tuncay Eşsiz](https://github.com/tunjayoff)  
Version: 1.1.0  
Last Updated: 26 December 2025
