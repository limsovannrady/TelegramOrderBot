# Telegram Bot Project

## Overview

This is a simple Telegram bot application written in Python that responds to the `/start` command with a Khmer language message. The bot uses the Telegram Bot API directly via HTTP requests to avoid library conflicts and ensures reliable operation. The bot runs as @Coupon2025_Robot.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

The application follows a simple HTTP-based architecture:

- **Main Bot Logic**: `telegram_bot_simple.py` contains the core bot functionality using direct API calls
- **Configuration Management**: Configuration is embedded in the main file for simplicity
- **Logging Strategy**: File-based and console logging with UTF-8 support for Khmer text
- **API Integration**: Uses `requests` library for direct HTTP communication with Telegram Bot API

The architecture avoids complex library dependencies and ensures reliable operation through direct API usage.

## Key Components

### Bot Functions
- **handle_message()**: Main message processor with state management
- **send_message()**: Direct API message sending
- **get_updates()**: Polling for new messages

### User Commands
- **/start**: Available to all users, sends Khmer account selection message with persistent inline keyboard
- **🧧គូប៉ុង E-GetS Button**: Persistent inline keyboard button that refreshes main message

### Admin Commands (ID: 5002402843)
- **/add_account**: Starts account addition workflow
  - Step 1: Input accounts in format "phone | password"
  - Step 2: Input account type
  - Step 3: Set price per account
  - Completion: Confirms addition with count, type, and price

### Session Management
- **user_sessions**: Tracks conversation state for multi-step workflows
- **accounts_data**: Stores account information, types, and pricing

### Logging System
- **Dual Output**: Logs to both console (stdout) and file (`bot.log`)
- **Unicode Support**: Proper UTF-8 encoding for Khmer text handling
- **Structured Logging**: Consistent format with timestamps and log levels

## Data Flow

1. **Bot Initialization**: Application starts with token from environment/config
2. **Command Processing**: User sends `/start` command to bot
3. **Message Response**: Bot replies with predefined Khmer message
4. **Logging**: All interactions and errors are logged with user information
5. **Error Handling**: Graceful error management with user-friendly messages

## External Dependencies

### Core Dependencies
- **requests**: HTTP library for API communication
- **Standard Library**: `logging`, `time`, `json`, `sys` for core functionality

### Bot Configuration
- `BOT_TOKEN`: Telegram bot authentication token (7512276458:AAHGerJbecGFUyZwXEY24-XtEmGuLvLFS_Y)
- `BOT_USERNAME`: @Coupon2025_Robot
- `KHMER_MESSAGE`: "ជ្រើសរើស Account ដើម្បីបញ្ជាទិញ"

### Third-party Services
- **Telegram Bot API**: Direct HTTP API integration for message handling and user interaction

## Deployment Strategy

The application is designed for simple deployment with minimal configuration:

### Environment Setup
- Python 3.x runtime required
- Bot token configuration through environment variables
- Unicode/UTF-8 support for Khmer text rendering

### Scalability Considerations
- Single-threaded design suitable for moderate traffic
- Stateless architecture allows for easy horizontal scaling
- File-based logging may need rotation for production use

### Current Limitations
- Incomplete error handler implementation
- Basic command structure (only `/start` implemented)
- No database integration for user data persistence
- No webhook support (polling-based operation assumed)

The architecture provides a solid foundation for a Telegram bot with proper separation of concerns and room for future enhancements like additional commands, user state management, and database integration.