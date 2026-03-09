#!/usr/bin/env python3
"""
Fyers Daily Summary Generator - Google Sheets Integration
Generates daily/weekly summaries from Google Sheets data
"""

import json
import os
import time
import threading
import requests
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("Environment variables loaded from .env file")
except ImportError:
    print("python-dotenv not installed. Install with: pip install python-dotenv")
except Exception as e:
    print(f"Could not load .env file: {e}")

# =============================================================================
# CONFIGURATION
# =============================================================================

# Google Sheets Configuration
GOOGLE_SHEETS_ID = "1kgrKVjUm0lB0fz-74Q_C-sXls7IyyqFDGhf8NZmGG4A"

# Summary Telegram Bot Configuration
SUMMARY_TELEGRAM_BOT_TOKEN = "8225228168:AAFVxVL_ygeTz8IDVIt7Qp1qlkra7qgoAKY"
SUMMARY_TELEGRAM_CHAT_ID = "8388919023"
SUMMARY_SEND_TIME = "16:30"  # 4:30 PM IST

# Load Google Credentials from Environment Variables
try:
    # Try to load credentials from environment variable
    google_creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if google_creds_json:
        # Process the JSON string to handle newlines properly
        GOOGLE_CREDENTIALS = json.loads(google_creds_json)
        # Ensure private key newlines are properly formatted
        if 'private_key' in GOOGLE_CREDENTIALS:
            GOOGLE_CREDENTIALS['private_key'] = GOOGLE_CREDENTIALS['private_key'].replace('\\n', '\n')
        print("Google Sheets credentials loaded from environment variable")
    else:
        # Fallback: try to load from individual environment variables
        private_key = os.getenv('GOOGLE_PRIVATE_KEY')
        if private_key:
            # Ensure newlines are properly handled in private key
            private_key = private_key.replace('\\n', '\n')
        
        GOOGLE_CREDENTIALS = {
            "type": "service_account",
            "project_id": os.getenv('GOOGLE_PROJECT_ID'),
            "private_key_id": os.getenv('GOOGLE_PRIVATE_KEY_ID'),
            "private_key": private_key,
            "client_email": os.getenv('GOOGLE_CLIENT_EMAIL'),
            "client_id": os.getenv('GOOGLE_CLIENT_ID'),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": os.getenv('GOOGLE_CLIENT_X509_CERT_URL'),
            "universe_domain": "googleapis.com"
        }
        
        # Check if all required fields are present
        required_fields = ['project_id', 'private_key_id', 'private_key', 'client_email', 'client_id']
        missing_fields = [field for field in required_fields if not GOOGLE_CREDENTIALS.get(field)]
        
        if missing_fields:
            print(f"Missing required Google credentials environment variables: {', '.join(missing_fields)}")
            GOOGLE_CREDENTIALS = None
        else:
            print("Google Sheets credentials loaded from individual environment variables")
            
except json.JSONDecodeError as e:
    print(f"Error parsing Google credentials JSON from environment: {e}")
    GOOGLE_CREDENTIALS = None
except Exception as e:
    print(f"Error loading Google credentials from environment: {e}")
    GOOGLE_CREDENTIALS = None

# =============================================================================
# SUMMARY TELEGRAM HANDLER
# =============================================================================

class SummaryTelegramHandler:
    def __init__(self):
        self.bot_token = SUMMARY_TELEGRAM_BOT_TOKEN
        self.chat_id = SUMMARY_TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        
    def send_message(self, message):
        """Send a message to Telegram"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data, timeout=10)
            if response.status_code == 200:
                print("Summary message sent successfully")
                return True
            else:
                print(f"Failed to send summary message: {response.text}")
                return False
        except Exception as e:
            print(f"Error sending summary message: {e}")
            return False
    
    def send_messages(self, messages):
        """Send multiple messages to Telegram"""
        try:
            success_count = 0
            for i, message in enumerate(messages, 1):
                print(f"\nSending message {i}/{len(messages)}...")
                if self.send_message(message):
                    success_count += 1
                    time.sleep(1)  # Small delay between messages
                else:
                    print(f"Failed to send message {i}")
            
            print(f"\nSent {success_count}/{len(messages)} messages successfully")
            return success_count == len(messages)
            
        except Exception as e:
            print(f"Error sending multiple messages: {e}")
            return False

# =============================================================================
# DAILY SUMMARY GENERATOR
# =============================================================================

class DailySummaryGenerator:
    def __init__(self):
        self.sheets_id = GOOGLE_SHEETS_ID
        self.credentials = GOOGLE_CREDENTIALS
        self.worksheet = None
    
    def get_current_datetime(self):
        """Get current datetime"""
        return datetime.now(ZoneInfo("Asia/Kolkata"))
        
    def initialize_sheets(self):
        """Initialize Google Sheets connection"""
        try:
            if not self.credentials:
                print("Google Sheets credentials not available")
                return False
            
            scopes = ['https://www.googleapis.com/auth/spreadsheets']
            creds = Credentials.from_service_account_info(self.credentials, scopes=scopes)
            client = gspread.authorize(creds)
            
            spreadsheet = client.open_by_key(self.sheets_id)
            self.worksheet = spreadsheet.sheet1
            
            print("Google Sheets initialized for summary")
            return True
        except Exception as e:
            print(f"Error initializing sheets for summary: {e}")
            return False
    
    def get_today_data(self):
        """Get all data for today's date from Google Sheets"""
        try:
            if not self.worksheet:
                if not self.initialize_sheets():
                    return []
            
            # Get current date in multiple formats
            now = self.get_current_datetime()
            today_formats = [
                now.strftime("%d-%m-%Y"),    # 15-10-2025
                now.strftime("%Y-%m-%d"),    # 2025-10-15
                now.strftime("%d/%m/%Y"),    # 15/10/2025
                now.strftime("%m/%d/%Y"),    # 10/15/2025
                now.strftime("%d-%m-%y"),    # 15-10-25
            ]
            
            # Get all values including headers
            all_values = self.worksheet.get_all_values()
            
            if not all_values or len(all_values) < 2:
                print("No data in sheet")
                return []
            
            # First row is headers
            headers = all_values[0]
            print(f"Sheet headers: {headers}")
            
            # Find column indices
            date_col_idx = None
            symbol_col_idx = None
            value_col_idx = None
            
            for idx, header in enumerate(headers):
                header_lower = header.lower().strip()
                
                # Match Date column
                if header_lower == 'date':
                    date_col_idx = idx
                    print(f"OK Found Date column at index {idx}")
                
                # Match Symbol column
                elif header_lower == 'symbol':
                    symbol_col_idx = idx
                    print(f"OK Found Symbol column at index {idx}")
                
                # Match Value column - looking for Trd_Val_Cr or similar
                elif ('trd' in header_lower and 'val' in header_lower and 'cr' in header_lower) or \
                     ('value' in header_lower and ('cr' in header_lower or 'crore' in header_lower)):
                    value_col_idx = idx
                    print(f"OK Found Value column at index {idx}: '{header}'")
            
            if date_col_idx is None or symbol_col_idx is None or value_col_idx is None:
                print(f"ERROR Required columns not found!")
                print(f"   Date column index: {date_col_idx}")
                print(f"   Symbol column index: {symbol_col_idx}")
                print(f"   Value column index: {value_col_idx}")
                print(f"\nINFO Looking for columns named:")
                print(f"   - 'Date' (exact match)")
                print(f"   - 'Symbol' (exact match)")
                print(f"   - 'Trd_Val_Cr' or 'Value (Rs Crores)' or similar")
                return []
            
            print(f"\nOK All required columns found!")
            print(f"  Date: column {date_col_idx}")
            print(f"  Symbol: column {symbol_col_idx}")
            print(f"  Value: column {value_col_idx} ('{headers[value_col_idx]}')")
            
            # Process data rows
            today_records = []
            
            for row_idx, row in enumerate(all_values[1:], start=2):  # Skip header row
                if len(row) <= max(date_col_idx, symbol_col_idx, value_col_idx):
                    continue
                
                date_value = str(row[date_col_idx]).strip()
                
                # Check if date matches today
                is_today = False
                for date_format in today_formats:
                    if date_value == date_format or date_value.startswith(date_format):
                        is_today = True
                        break
                
                if is_today:
                    symbol = str(row[symbol_col_idx]).strip()
                    value_str = str(row[value_col_idx]).strip()
                    
                    record = {
                        'Date': date_value,
                        'Symbol': symbol,
                        'Trd_Val_Cr': value_str
                    }
                    today_records.append(record)
            
            print(f"\nData Summary:")
            print(f"   Total rows in sheet: {len(all_values) - 1}")
            print(f"   Records for today ({today_formats[0]}): {len(today_records)}")
            
            # Debug: Print first few records
            if today_records:
                print(f"\nSample records (first 3):")
                for i, record in enumerate(today_records[:3], 1):
                    print(f"   {i}. {record['Date']} | {record['Symbol']:20s} | Rs.{record['Trd_Val_Cr']} Cr")
            else:
                print(f"\nWARNING No records found for today's date: {today_formats[0]}")
                print(f"   Sample dates in sheet:")
                for row in all_values[1:6]:  # Show first 5 dates
                    if len(row) > date_col_idx:
                        print(f"   - {row[date_col_idx]}")
            
            return today_records
            
        except Exception as e:
            print(f"ERROR getting today's data: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_date_range_data(self, days_back=0):
        """Get data for a specific date range"""
        try:
            if not self.worksheet:
                if not self.initialize_sheets():
                    return []
            
            # Calculate target dates
            now = self.get_current_datetime()
            target_dates = []
            
            for i in range(days_back + 1):
                target_date = now - timedelta(days=i)
                date_formats = [
                    target_date.strftime("%d-%m-%Y"),    # 15-10-2025
                    target_date.strftime("%Y-%m-%d"),    # 2025-10-15
                    target_date.strftime("%d/%m/%Y"),    # 15/10/2025
                    target_date.strftime("%m/%d/%Y"),    # 10/15/2025
                    target_date.strftime("%d-%m-%y"),    # 15-10-25
                ]
                target_dates.extend(date_formats)
            
            # Get all values including headers
            all_values = self.worksheet.get_all_values()
            
            if not all_values or len(all_values) < 2:
                print("No data in sheet")
                return []
            
            # First row is headers
            headers = all_values[0]
            
            # Find column indices (same logic as get_today_data)
            date_col_idx = None
            symbol_col_idx = None
            value_col_idx = None
            
            for idx, header in enumerate(headers):
                header_lower = header.lower().strip()
                
                if header_lower == 'date':
                    date_col_idx = idx
                elif header_lower == 'symbol':
                    symbol_col_idx = idx
                elif ('trd' in header_lower and 'val' in header_lower and 'cr' in header_lower) or \
                     ('value' in header_lower and ('cr' in header_lower or 'crore' in header_lower)):
                    value_col_idx = idx
            
            if date_col_idx is None or symbol_col_idx is None or value_col_idx is None:
                print(f"ERROR Required columns not found for date range!")
                return []
            
            # Process data rows
            date_range_records = []
            
            for row_idx, row in enumerate(all_values[1:], start=2):
                if len(row) > max(date_col_idx, symbol_col_idx, value_col_idx):
                    date_val = row[date_col_idx].strip()
                    symbol_val = row[symbol_col_idx].strip()
                    value_val = row[value_col_idx].strip()
                    
                    if date_val in target_dates and symbol_val and value_val:
                        try:
                            trd_val_cr = float(value_val.replace(',', '')) if value_val else 0.0
                            record = {
                                'Date': date_val,
                                'Symbol': symbol_val,
                                'Trd_Val_Cr': trd_val_cr
                            }
                            date_range_records.append(record)
                        except ValueError:
                            continue
            
            return date_range_records
            
        except Exception as e:
            print(f"ERROR getting date range data: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def generate_top_15_summary(self, days_back=0, summary_type="Daily"):
        """Generate top 15 stocks summary based on count and total value"""
        try:
            print("\n" + "="*70)
            print(f"GENERATING TOP 15 {summary_type.upper()} SUMMARY")
            print("="*70)
            
            # Step 1: Get records based on date range
            if days_back == 0:
                records = self.get_today_data()
            else:
                records = self.get_date_range_data(days_back)
            
            if not records:
                print(f"ERROR No records found for {summary_type.lower()} - cannot generate summary")
                return None
            
            print(f"\nOK Processing {len(records)} records for {summary_type.lower()} summary...")
            
            # Step 2: Process each record
            symbol_stats = {}
            parse_success = 0
            parse_failed = 0
            
            for record in records:
                # Get symbol
                symbol = record.get('Symbol', '').strip()
                
                # Get trade value
                value_str = str(record.get('Trd_Val_Cr', '0')).strip()
                
                # Skip if symbol is empty or invalid
                if not symbol or symbol == 'Unknown' or symbol == '':
                    continue
                
                # Parse the trade value
                try:
                    # Remove any non-numeric characters except decimal point and minus sign
                    value_clean = re.sub(r'[^\d.\-]', '', value_str)
                    trd_val = float(value_clean) if value_clean and value_clean != '-' else 0.0
                    
                    if trd_val > 0:
                        parse_success += 1
                        if parse_success <= 5:  # Show first 5 successful parses
                            print(f"   OK {symbol:20s}: '{value_str}' -> {trd_val:.2f} Cr")
                except (ValueError, AttributeError) as e:
                    trd_val = 0.0
                    parse_failed += 1
                    if parse_failed <= 3:  # Show first 3 failures
                        print(f"   ERROR {symbol:20s}: Failed to parse '{value_str}'")
                
                # Initialize or update symbol stats
                if symbol not in symbol_stats:
                    symbol_stats[symbol] = {
                        'count': 0,
                        'total_trd_val_cr': 0.0
                    }
                
                # Increment count
                symbol_stats[symbol]['count'] += 1
                
                # Add to total value
                symbol_stats[symbol]['total_trd_val_cr'] += trd_val
            
            print(f"\nParsing Results:")
            print(f"   OK Successfully parsed: {parse_success}")
            print(f"   ERROR Failed to parse: {parse_failed}")
            print(f"   INFO Unique symbols: {len(symbol_stats)}")
            
            # Step 3: Sort by count and get top 15
            sorted_symbols = sorted(
                symbol_stats.items(),
                key=lambda x: x[1]['count'],
                reverse=True
            )
            
            top_15 = sorted_symbols[:15]
            
            print(f"\n{'='*70}")
            print("TOP 15 SYMBOLS BY COUNT")
            print(f"{'='*70}")
            print(f"{'Rank':<6} {'Symbol':<20} {'Count':<8} {'Total Value (Cr)':<18} {'Avg/Trade (Cr)'}")
            print("-"*70)
            
            for i, (symbol, stats) in enumerate(top_15, 1):
                count = stats['count']
                total_val = stats['total_trd_val_cr']
                avg_val = total_val / count if count > 0 else 0
                
                print(f"{i:2d}.   {symbol:<20} {count:<8} Rs.{total_val:>15,.2f}  Rs.{avg_val:>10,.2f}")
            
            print("="*70 + "\n")
            
            return top_15, len(records), len(symbol_stats)
            
        except Exception as e:
            print(f"ERROR generating summary: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def format_single_summary_message(self, days_back=0, summary_type="Daily"):
        """Format a single summary message for Telegram"""
        try:
            result = self.generate_top_15_summary(days_back, summary_type)
            
            if not result:
                return f"No volume spike data available for {summary_type.lower()}'s summary"
            
            top_15, total_records, unique_symbols = result
            
            # Get date range info
            now = self.get_current_datetime()
            if days_back == 0:
                date_info = now.strftime("%d-%m-%Y")
            else:
                end_date = now
                start_date = now - timedelta(days=days_back)
                date_info = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}"
            
            # Calculate total value across top 15 stocks
            total_top15_value = sum(stats['total_trd_val_cr'] for _, stats in top_15)
            
            message = f"""<b>{summary_type} Volume Spike Summary</b>
Date: {date_info}
Total Records: {total_records}
Unique Symbols: {unique_symbols}
Top 15 Total Value: Rs.{total_top15_value:,.2f} Cr

<b>TOP 15 RANKINGS (by Count):</b>

"""
            
            for idx, (symbol, stats) in enumerate(top_15, 1):
                count = stats['count']
                total_trd_val_cr = stats['total_trd_val_cr']
                avg_per_trade = total_trd_val_cr / count if count > 0 else 0
                
                message += f"""{idx}. <b>{symbol}</b>
   Count: <b>{count}</b> trades
   Total Value: Rs.{total_trd_val_cr:,.2f} Cr
   Avg per Trade: Rs.{avg_per_trade:.2f} Cr
   
"""
            
            message += f"""====================
<i>Analysis Complete for {date_info}</i>
<i>Ranked by highest trade count</i>
<i>Values from Trd_Val_Cr column</i>
"""
            
            return message
            
        except Exception as e:
            print(f"ERROR formatting {summary_type.lower()} summary message: {e}")
            import traceback
            traceback.print_exc()
            return f"ERROR generating {summary_type.lower()} summary: {str(e)}"
    
    def format_summary_message(self):
        """Format the summary message for Telegram with day-specific logic"""
        try:
            now = self.get_current_datetime()
            current_day = now.strftime("%A")  # Monday, Tuesday, etc.
            
            print(f"Today is {current_day} - determining summary types...")
            
            messages = []
            
            # Always send daily summary
            print("Generating daily summary...")
            daily_summary = self.format_single_summary_message(0, "Daily")
            messages.append(daily_summary)
            
            # Wednesday and Friday: Add 3-day summary
            if current_day in ["Wednesday", "Friday"]:
                print("Generating 3-day summary...")
                three_day_summary = self.format_single_summary_message(2, "3-Day")
                messages.append(three_day_summary)
            
            # Friday only: Add weekly summary
            if current_day == "Friday":
                print("Generating weekly summary...")
                weekly_summary = self.format_single_summary_message(4, "Weekly")
                messages.append(weekly_summary)
            
            print(f"Generated {len(messages)} summary types")
            return messages
            
        except Exception as e:
            print(f"ERROR formatting summary message: {e}")
            import traceback
            traceback.print_exc()
            return [f"ERROR generating summary: {str(e)}"]

# =============================================================================
# SUMMARY SCHEDULER
# =============================================================================

def summary_scheduler():
    """Background thread to handle summary sending"""
    print("Summary scheduler started")
    
    summary_handler = SummaryTelegramHandler()
    summary_generator = DailySummaryGenerator()
    
    last_sent_date = None
    
    # Send summary immediately on startup for testing
    print("\n" + "="*50)
    print("SENDING IMMEDIATE SUMMARY ON STARTUP")
    print("="*50)
    try:
        summary_messages = summary_generator.format_summary_message()
        if summary_handler.send_messages(summary_messages):
            print("Initial summary sent successfully")
        else:
            print("Failed to send initial summary")
    except Exception as e:
        print(f"Error sending initial summary: {e}")
    print("="*50 + "\n")
    
    while True:
        try:
            now = summary_generator.get_current_datetime()
            current_date = now.strftime("%d-%m-%Y")
            current_time = now.strftime("%H:%M")
            
            # Reset flag for new day
            if last_sent_date != current_date:
                last_sent_date = None
                print(f"New day started: {current_date}")
            
            # Print status every 5 minutes
            if now.minute % 5 == 0 and now.second < 10:
                print(f"[{current_time}] Scheduler running... (waiting for {SUMMARY_SEND_TIME})")
                if last_sent_date:
                    print(f"   Summary already sent today at 16:30")
            
            # Send summary once at 16:30 IST
            if current_time == SUMMARY_SEND_TIME and last_sent_date != current_date:
                print(f"Sending summary at {current_time}")
                
                summary_messages = summary_generator.format_summary_message()
                
                if summary_handler.send_messages(summary_messages):
                    last_sent_date = current_date
                    print(f"Summary sent successfully at {current_time} on {current_date}")
                else:
                    print(f"Failed to send summary at {current_time}")
            
            time.sleep(60)
            
        except Exception as e:
            print(f"Error in summary scheduler: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)

# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    try:
        print("="*50)
        print("Fyers Daily Summary Generator")
        print("="*50)
        
        summary_scheduler()
        
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()