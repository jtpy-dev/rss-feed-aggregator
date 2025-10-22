import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configuration
FEED_SOURCES = [
    {
        'url': 'https://www.accc.gov.au/rss/news_centre.xml',
        'name': 'ACCC News',
        'type': 'rss'
    },
    {
        'url': 'https://www.austrac.gov.au/media-release/rss.xml',
        'name': 'AUSTRAC Media Releases',
        'type': 'rss'
    },
    {
        'url': 'https://www.apra.gov.au/news-and-publications',
        'name': 'APRA News',
        'type': 'webpage'
    },
    {
        'url': 'https://www.asic.gov.au/newsroom/media-releases/',
        'name': 'ASIC Media Releases',
        'type': 'webpage-selenium'
    },
    {
        'url': 'https://www.rba.gov.au/media-releases/',
        'name': 'RBA Media Releases',
        'type': 'webpage'
    }
]

ARTICLES_PER_SOURCE = 10  # Fetch 10 latest articles from each source
OUTPUT_HTML = 'index.html'
OUTPUT_XML = 'feed-data.xml'

def extract_date_from_text(text):
    """Extract the FIRST date from text content"""
    import re
    
    # Common date patterns - will return the FIRST match found
    date_patterns = [
        (r'\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b', '%d %B %Y'),
        (r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})\b', '%d %b %Y'),
        (r'\b(\d{1,2}/\d{1,2}/\d{4})\b', '%d/%m/%Y'),
        (r'\b(\d{4}-\d{2}-\d{2})\b', '%Y-%m-%d'),
    ]
    
    for pattern, date_format in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                date_str = match.group(1)
                # Validate it's a real date
                datetime.strptime(date_str, date_format)
                return date_str  # Return FIRST valid date found
            except:
                continue
    
    return None

def fetch_full_text(url):
    """Fetch and extract full text from article URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)  # Increased timeout to 30 seconds
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(['script', 'style', 'nav', 'header', 'footer']):
            script.decompose()
        
        # Try to find main content area
        content = None
        for selector in ['article', '.content', '.article-content', 'main', '.main-content']:
            content = soup.select_one(selector)
            if content:
                break
        
        if not content:
            content = soup.find('body')
        
        if content:
            # Get text and clean it up
            text = content.get_text(separator='\n', strip=True)
            # Remove excessive newlines
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            return '\n'.join(lines)
        
        return "Content not available"
    except Exception as e:
        print(f"Error fetching full text from {url}: {e}")
        return f"Error fetching content: {str(e)}"

def fetch_asic_news_selenium():
    """Scrape ASIC media releases page using Selenium (JavaScript required)"""
    driver = None
    try:
        # Set up Chrome options for headless mode
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # Initialize the driver
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(60)
        
        url = 'https://www.asic.gov.au/newsroom/media-releases/'
        print(f"  Loading {url} with Selenium...")
        driver.get(url)
        
        # Wait for the nr-list to be present (up to 30 seconds)
        print("  Waiting for page to load...")
        wait = WebDriverWait(driver, 30)
        nr_list = wait.until(EC.presence_of_element_located((By.ID, "nr-list")))
        
        # Give JavaScript a moment to fully render
        time.sleep(2)
        
        # Get the page source and parse with BeautifulSoup
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Find the ul#nr-list
        nr_list = soup.find('ul', id='nr-list')
        
        if not nr_list:
            print("  ERROR: Could not find ul#nr-list even after JavaScript rendering")
            return []
        
        articles = []
        news_items = nr_list.find_all('li', recursive=False)
        
        print(f"  Found {len(news_items)} ASIC items in ul#nr-list")
        
        # Process up to 10 items
        for item in news_items[:10]:
            try:
                # Find h3 > a
                h3_tag = item.find('h3')
                if not h3_tag:
                    continue
                
                title_elem = h3_tag.find('a')
                if not title_elem:
                    continue
                
                article_url = title_elem.get('href', '')
                if article_url.startswith('/'):
                    article_url = 'https://www.asic.gov.au' + article_url
                
                title = title_elem.get_text(strip=True)
                
                # Try to find date
                date_text = ''
                info_div = item.find('div', class_='nh-list-info')
                if info_div:
                    date_text = extract_date_from_text(info_div.get_text())
                
                if not date_text:
                    # Try to extract from full item text
                    date_text = extract_date_from_text(item.get_text())
                
                article = {
                    'title': title,
                    'link': article_url,
                    'published': date_text or '',
                    'summary': '',
                }
                articles.append(article)
                print(f"    Found ASIC article: {title[:70]}...")
                
            except Exception as e:
                print(f"  Error parsing ASIC item: {e}")
                continue
        
        print(f"  Successfully parsed {len(articles)} ASIC articles")
        return articles
        
    except Exception as e:
        print(f"Error fetching ASIC news with Selenium: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        if driver:
            driver.quit()

def fetch_apra_news():
    """Scrape APRA news page to create RSS-like entries"""
    try:
        url = 'https://www.apra.gov.au/news-and-publications'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)  # Increased timeout to 30 seconds
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        articles = []
        
        # Try multiple selectors for APRA's page structure
        news_items = soup.select('.view-content .views-row')
        
        if not news_items:
            # Try alternative selectors
            news_items = soup.select('article, .node, .item, .news-item')
        
        if not news_items:
            # Try finding links with dates nearby
            news_items = soup.select('.views-row, [class*="news"], [class*="publication"]')
        
        print(f"  Found {len(news_items)} potential APRA items")
        
        # Limit to 10 articles
        for item in news_items[:10]:
            try:
                # Try multiple selectors for title/link
                title_elem = item.select_one('h3 a, h2 a, .title a, a[href*="/news"], a[href*="/publication"]')
                
                if not title_elem:
                    # Try finding any link in the item
                    title_elem = item.find('a')
                
                if not title_elem:
                    continue
                
                link_elem = title_elem
                
                # Try multiple date selectors
                date_elem = item.select_one(
                    '.date, time, .views-field-created, .field--name-created, '
                    '.views-field-field-date, [class*="date"]'
                )
                
                # If no date element, try to extract from text
                date_text = ''
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                else:
                    item_text = item.get_text()
                    date_text = extract_date_from_text(item_text)
                
                summary_elem = item.select_one('.summary, .views-field-body, .field--name-body, p')
                
                article_url = link_elem.get('href', '')
                if article_url.startswith('/'):
                    article_url = 'https://www.apra.gov.au' + article_url
                
                # Only add if it looks like a real article URL
                if article_url and ('apra.gov.au' in article_url):
                    article = {
                        'title': title_elem.get_text(strip=True),
                        'link': article_url,
                        'published': date_text or '',
                        'summary': summary_elem.get_text(strip=True)[:300] if summary_elem else '',
                    }
                    articles.append(article)
                    print(f"    Found APRA article: {article['title'][:50]}...")
            except Exception as e:
                print(f"  Error parsing APRA item: {e}")
                continue
        
        print(f"  Successfully parsed {len(articles)} APRA articles")
        return articles
    except Exception as e:
        print(f"Error fetching APRA news: {e}")
        return []

def fetch_apra_news():
    """Scrape APRA news page to create RSS-like entries"""
    try:
        url = 'https://www.apra.gov.au/news-and-publications'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        articles = []
        
        # Try multiple selectors for APRA's page structure
        news_items = soup.select('.view-content .views-row')
        
        if not news_items:
            # Try alternative selectors
            news_items = soup.select('article, .node, .item, .news-item')
        
        if not news_items:
            # Try finding links with dates nearby
            news_items = soup.select('.views-row, [class*="news"], [class*="publication"]')
        
        print(f"  Found {len(news_items)} potential APRA items")
        
        # Limit to 10 articles
        for item in news_items[:10]:
            try:
                # Try multiple selectors for title/link
                title_elem = item.select_one('h3 a, h2 a, .title a, a[href*="/news"], a[href*="/publication"]')
                
                if not title_elem:
                    # Try finding any link in the item
                    title_elem = item.find('a')
                
                if not title_elem:
                    continue
                
                link_elem = title_elem
                
                # Try multiple date selectors
                date_elem = item.select_one(
                    '.date, time, .views-field-created, .field--name-created, '
                    '.views-field-field-date, [class*="date"]'
                )
                
                # If no date element, try to extract from text
                date_text = ''
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                else:
                    item_text = item.get_text()
                    date_text = extract_date_from_text(item_text)
                
                summary_elem = item.select_one('.summary, .views-field-body, .field--name-body, p')
                
                article_url = link_elem.get('href', '')
                if article_url.startswith('/'):
                    article_url = 'https://www.apra.gov.au' + article_url
                
                # Only add if it looks like a real article URL
                if article_url and ('apra.gov.au' in article_url):
                    article = {
                        'title': title_elem.get_text(strip=True),
                        'link': article_url,
                        'published': date_text or '',
                        'summary': summary_elem.get_text(strip=True)[:300] if summary_elem else '',
                    }
                    articles.append(article)
                    print(f"    Found APRA article: {article['title'][:50]}...")
            except Exception as e:
                print(f"  Error parsing APRA item: {e}")
                continue
        
        print(f"  Successfully parsed {len(articles)} APRA articles")
        return articles
    except Exception as e:
        print(f"Error fetching APRA news: {e}")
        return []

def fetch_rba_news():
    """Scrape RBA media releases page to create RSS-like entries"""
    try:
        url = 'https://www.rba.gov.au/media-releases/'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        articles = []
        
        # RBA uses <ul class="list-articles rss-mr-list">
        media_releases_list = soup.select_one('ul.list-articles.rss-mr-list')
        
        if not media_releases_list:
            print("  ERROR: Could not find ul.list-articles.rss-mr-list container")
            return []
        
        # Find all list items with class="item rss-mr-item"
        news_items = media_releases_list.select('li.item.rss-mr-item')
        
        print(f"  Found {len(news_items)} potential RBA items in list-articles")
        
        # Limit to 10 articles
        for item in news_items[:10]:
            try:
                # Find the title div and link inside it
                title_div = item.select_one('div.title')
                if not title_div:
                    continue
                
                title_elem = title_div.find('a')
                if not title_elem:
                    continue
                
                article_url = title_elem.get('href', '')
                if article_url.startswith('/'):
                    article_url = 'https://www.rba.gov.au' + article_url
                
                title = title_elem.get_text(strip=True)
                
                # Try to find date in the item
                date_elem = item.select_one('.date, time, span')
                date_text = ''
                if date_elem and date_elem != title_elem:
                    date_text = date_elem.get_text(strip=True)
                    print(f"    Found date element: {date_text}")
                else:
                    # Try to extract from item text
                    item_text = item.get_text()
                    date_text = extract_date_from_text(item_text)
                    if date_text:
                        print(f"    Extracted date from item text: {date_text}")
                
                article = {
                    'title': title,
                    'link': article_url,
                    'published': date_text or '',
                    'summary': '',
                }
                articles.append(article)
                print(f"    Found RBA article: {title[:70]}...")
                    
            except Exception as e:
                print(f"  Error parsing RBA item: {e}")
                continue
        
        print(f"  Successfully parsed {len(articles)} RBA articles")
        return articles
    except Exception as e:
        print(f"Error fetching RBA news: {e}")
        return []

def parse_rss_feed(url):
    """Parse RSS feed and extract entries with retry logic"""
    max_attempts = 3
    timeout = 60  # Increased to 60 seconds
    
    for attempt in range(max_attempts):
        try:
            # Fetch the feed with a timeout first
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            print(f"  Attempt {attempt + 1}/{max_attempts} to fetch RSS feed...")
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            
            # Parse the fetched content
            feed = feedparser.parse(response.content)
            articles = []
            
            for entry in feed.entries[:10]:  # Limit to 10 articles per feed
                article = {
                    'title': entry.get('title', 'No title'),
                    'link': entry.get('link', ''),
                    'published': entry.get('published', entry.get('updated', '')),
                    'summary': entry.get('summary', entry.get('description', ''))
                }
                articles.append(article)
            
            print(f"  Successfully fetched RSS feed on attempt {attempt + 1}")
            return articles
            
        except requests.Timeout:
            print(f"  Timeout error on attempt {attempt + 1}/{max_attempts}")
            if attempt < max_attempts - 1:
                wait_time = 5
                print(f"  Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                print(f"  Failed to fetch RSS feed after {max_attempts} attempts")
                return []
                
        except Exception as e:
            print(f"  Error on attempt {attempt + 1}/{max_attempts}: {e}")
            if attempt < max_attempts - 1:
                wait_time = 5
                print(f"  Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                print(f"  Failed to fetch RSS feed after {max_attempts} attempts: {e}")
                return []
    
    return []

def process_feeds():
    """Process all feeds and extract full text"""
    all_articles = []
    
    for source in FEED_SOURCES:
        print(f"Processing {source['name']}...")
        
        if source['type'] == 'rss':
            articles = parse_rss_feed(source['url'])
        elif source['type'] == 'webpage-selenium':
            if 'asic.gov.au' in source['url']:
                articles = fetch_asic_news_selenium()
            else:
                continue
        elif source['type'] == 'webpage':
            if 'apra.gov.au' in source['url']:
                articles = fetch_apra_news()
            elif 'rba.gov.au' in source['url']:
                articles = fetch_rba_news()
            else:
                continue
        else:
            continue
        
        # Add source name and fetch full text
        for article in articles:
            article['source'] = source['name']
            print(f"  Fetching full text for: {article['title'][:50]}...")
            article['full_text'] = fetch_full_text(article['link'])
            
            # If date is missing, try to extract from full text
            if not article['published']:
                date_from_text = extract_date_from_text(article['full_text'])
                if date_from_text:
                    article['published'] = date_from_text
                    print(f"    Extracted date from text: {date_from_text}")
            
            time.sleep(0.5)  # Be polite, don't hammer servers
        
        all_articles.extend(articles)
    
    # Sort by date (newest first) - no limit, showing 10 from each source
    all_articles.sort(key=lambda x: parse_date(x['published']), reverse=True)
    return all_articles

def parse_date(date_str):
    """Parse date string to datetime object (timezone-naive)"""
    if not date_str:
        return datetime.min
    
    # Try common date formats
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%d',
        '%d %B %Y',
        '%d %b %Y',
        '%d/%m/%Y',
        '%Y-%m-%dT%H:%M:%S'
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # Remove timezone info to make all dates comparable
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except:
            continue
    
    # If no format matches, return datetime.min
    return datetime.min

def format_date(date_str):
    """Format date for display"""
    dt = parse_date(date_str)
    if dt == datetime.min:
        return date_str or 'Unknown'
    return dt.strftime('%d %b %Y')

def generate_html(articles):
    """Generate static HTML page"""
    
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Australian Financial Regulators Feed</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --primary: #3b82f6;
            --primary-dark: #2563eb;
            --secondary: #64748b;
            --text-primary: #f1f5f9;
            --text-secondary: #cbd5e1;
            --border: #334155;
            --bg-primary: #1e293b;
            --bg-secondary: #0f172a;
            --bg-tertiary: #334155;
            --bg-page: #0f172a;
            --shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
            --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(to bottom, #0f172a 0%, #020617 100%);
            min-height: 100vh;
            color: var(--text-primary);
            line-height: 1.6;
            padding: 0;
        }

        .nav-bar {
            background: var(--bg-primary);
            border-bottom: 1px solid var(--border);
            padding: 1rem 0;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(10px);
            box-shadow: var(--shadow);
        }

        .nav-container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .logo {
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--text-primary);
            letter-spacing: -0.025em;
        }

        .nav-right {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .last-updated-badge {
            padding: 0.375rem 0.75rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }

        .nav-link {
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.875rem;
            font-weight: 500;
            transition: all 0.2s;
            border: 1px solid transparent;
        }

        .nav-link:hover {
            background: var(--bg-secondary);
            color: var(--primary);
        }

        .container {
            max-width: 1400px;
            margin: 2rem auto;
            padding: 0 2rem 3rem;
        }

        .header-section {
            margin-bottom: 2rem;
        }

        .page-title {
            font-size: 2rem;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 0.75rem;
            letter-spacing: -0.025em;
        }

        .content-card {
            background: var(--bg-primary);
            border-radius: 1rem;
            overflow: hidden;
            border: 1px solid var(--border);
            box-shadow: var(--shadow-lg);
        }

        .table-wrapper {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        thead {
            background: linear-gradient(to bottom, #334155, #1e293b);
        }

        th {
            padding: 1rem;
            text-align: left;
            font-weight: 600;
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #ffffff;
            position: sticky;
            top: 0;
            z-index: 10;
            cursor: pointer;
            user-select: none;
        }

        th:hover {
            background: rgba(59, 130, 246, 0.1);
        }

        .sort-icon {
            display: inline-block;
            margin-left: 0.5rem;
            opacity: 0.3;
            font-size: 0.75rem;
        }

        th.sorted .sort-icon {
            opacity: 1;
        }

        tbody tr {
            border-bottom: 1px solid var(--border);
            transition: background 0.15s;
        }

        tbody tr:hover {
            background: var(--bg-secondary);
        }

        tbody tr:last-child {
            border-bottom: none;
        }

        td {
            padding: 1.25rem 1rem;
            vertical-align: top;
        }

        .source-tag {
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.75rem;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.025em;
            margin-bottom: 0.5rem;
        }

        .source-tag.accc {
            background: #1e3a8a;
            color: #93c5fd;
        }

        .source-tag.austrac {
            background: #14532d;
            color: #86efac;
        }

        .source-tag.apra {
            background: #713f12;
            color: #fde047;
        }

        .source-tag.asic {
            background: #7c2d12;
            color: #fca5a5;
        }

        .source-tag.rba {
            background: #3730a3;
            color: #c4b5fd;
        }

        .article-title {
            color: var(--text-primary);
            font-weight: 600;
            font-size: 0.9375rem;
            text-decoration: none;
            display: block;
            line-height: 1.5;
            transition: color 0.2s;
        }

        .article-title:hover {
            color: var(--primary);
        }

        .article-date {
            color: var(--text-secondary);
            font-size: 0.8125rem;
            font-weight: 500;
        }

        .article-content {
            color: var(--text-secondary);
            font-size: 0.875rem;
            line-height: 1.7;
            max-height: 20rem;
            overflow-y: auto;
            padding: 1rem;
            background: var(--bg-secondary);
            border-radius: 0.5rem;
            border-left: 3px solid var(--primary);
        }

        .article-content::-webkit-scrollbar {
            width: 6px;
        }

        .article-content::-webkit-scrollbar-track {
            background: var(--bg-tertiary);
            border-radius: 3px;
        }

        .article-content::-webkit-scrollbar-thumb {
            background: var(--secondary);
            border-radius: 3px;
        }

        .article-content::-webkit-scrollbar-thumb:hover {
            background: var(--text-secondary);
        }

        .footer {
            margin-top: 3rem;
            padding: 2rem;
            text-align: center;
            color: var(--text-secondary);
            font-size: 0.875rem;
            background: var(--bg-primary);
            border-radius: 0.75rem;
            border: 1px solid var(--border);
        }

        .footer-divider {
            height: 1px;
            background: var(--border);
            margin: 1rem auto;
            max-width: 12rem;
        }

        .filter-section {
            margin-bottom: 2rem;
            padding: 1.25rem;
            background: var(--bg-primary);
            border-radius: 0.75rem;
            border: 1px solid var(--border);
            box-shadow: var(--shadow);
            display: flex;
            align-items: center;
            gap: 1rem;
            flex-wrap: wrap;
        }

        .filter-label {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .filter-buttons {
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }

        .filter-btn {
            padding: 0.5rem 1rem;
            border: 1px solid var(--border);
            background: var(--bg-secondary);
            color: var(--text-secondary);
            border-radius: 0.5rem;
            font-size: 0.875rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            font-family: inherit;
        }

        .filter-btn:hover {
            background: var(--bg-tertiary);
            border-color: var(--primary);
        }

        .filter-btn.active {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }

        .article-row {
            transition: opacity 0.2s, transform 0.2s;
        }

        .article-row.hidden {
            display: none;
        }

        @media (max-width: 768px) {
            .container {
                padding: 0 1rem 2rem;
                margin: 1rem auto;
            }

            .nav-container {
                padding: 0 1rem;
                flex-wrap: wrap;
            }

            .nav-right {
                flex-wrap: wrap;
            }

            .page-title {
                font-size: 1.5rem;
            }

            .filter-section {
                flex-direction: column;
                align-items: flex-start;
            }

            .filter-buttons {
                width: 100%;
            }

            .filter-btn {
                flex: 1;
                min-width: fit-content;
            }

            th, td {
                padding: 0.75rem 0.5rem;
                font-size: 0.8125rem;
            }

            .article-content {
                max-height: 15rem;
            }
        }
    </style>
</head>
<body>
    <nav class="nav-bar">
        <div class="nav-container">
            <div class="logo">Financial Regulators Feed</div>
            <div class="nav-right">
                <span class="last-updated-badge">Last Updated: ''' + datetime.now().strftime('%d %b %Y %H:%M') + '''</span>
                <a href="feed-data.xml" class="nav-link" download>Export XML</a>
            </div>
        </div>
    </nav>

    <div class="container">
        <div class="header-section">
            <h1 class="page-title">Latest Regulatory Updates</h1>
        </div>

        <div class="filter-section">
            <span class="filter-label">Filter by Source:</span>
            <div class="filter-buttons">
                <button class="filter-btn active" data-filter="all" onclick="filterArticles('all')">All Sources</button>
                <button class="filter-btn" data-filter="ACCC News" onclick="filterArticles('ACCC News')">ACCC</button>
                <button class="filter-btn" data-filter="ASIC Media Releases" onclick="filterArticles('ASIC Media Releases')">ASIC</button>
                <button class="filter-btn" data-filter="APRA News" onclick="filterArticles('APRA News')">APRA</button>
                <button class="filter-btn" data-filter="AUSTRAC Media Releases" onclick="filterArticles('AUSTRAC Media Releases')">AUSTRAC</button>
                <button class="filter-btn" data-filter="RBA Media Releases" onclick="filterArticles('RBA Media Releases')">RBA</button>
            </div>
        </div>

        <div class="content-card">
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 28%;" onclick="sortTable('title')">
                                Title
                                <span class="sort-icon">▼</span>
                            </th>
                            <th style="width: 12%;" onclick="sortTable('date')">
                                Date
                                <span class="sort-icon">▼</span>
                            </th>
                            <th style="width: 60%;">Content</th>
                        </tr>
                    </thead>
                    <tbody id="articleTableBody">
'''
    
    for article in articles:
        source_class = article['source'].lower().split()[0]
        html += f'''
                        <tr class="article-row" data-source="{article['source']}" data-title="{article['title'].lower()}" data-date="{article['published']}">
                            <td>
                                <span class="source-tag {source_class}">{article['source']}</span>
                                <a href="{article['link']}" target="_blank" class="article-title">{article['title']}</a>
                            </td>
                            <td>
                                <span class="article-date">{format_date(article['published'])}</span>
                            </td>
                            <td>
                                <div class="article-content">{article['full_text']}</div>
                            </td>
                        </tr>
'''
    
    html += '''
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <p>Displaying latest articles from ACCC, ASIC, APRA, AUSTRAC, and RBA</p>
            <div class="footer-divider"></div>
            <p>Automatically updated every 12 hours</p>
        </div>
    </div>

    <script>
        let currentSort = { column: null, ascending: true };

        function filterArticles(source) {
            const rows = document.querySelectorAll('.article-row');
            const buttons = document.querySelectorAll('.filter-btn');
            
            buttons.forEach(btn => {
                if (btn.dataset.filter === source) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            
            rows.forEach(row => {
                const rowSource = row.dataset.source;
                if (source === 'all' || rowSource === source) {
                    row.classList.remove('hidden');
                } else {
                    row.classList.add('hidden');
                }
            });
        }

        function sortTable(column) {
            const tbody = document.getElementById('articleTableBody');
            const rows = Array.from(tbody.querySelectorAll('.article-row'));
            
            // Toggle sort direction if clicking same column
            if (currentSort.column === column) {
                currentSort.ascending = !currentSort.ascending;
            } else {
                currentSort.column = column;
                currentSort.ascending = true;
            }
            
            // Update header indicators
            document.querySelectorAll('th').forEach(th => {
                th.classList.remove('sorted');
                const icon = th.querySelector('.sort-icon');
                if (icon) icon.textContent = '▼';
            });
            
            const activeHeader = document.querySelector(`th[onclick="sortTable('${column}')"]`);
            activeHeader.classList.add('sorted');
            const activeIcon = activeHeader.querySelector('.sort-icon');
            activeIcon.textContent = currentSort.ascending ? '▲' : '▼';
            
            // Sort rows
            rows.sort((a, b) => {
                let aValue, bValue;
                
                if (column === 'title') {
                    aValue = a.dataset.title;
                    bValue = b.dataset.title;
                } else if (column === 'date') {
                    aValue = new Date(a.dataset.date || '1970-01-01');
                    bValue = new Date(b.dataset.date || '1970-01-01');
                }
                
                if (aValue < bValue) return currentSort.ascending ? -1 : 1;
                if (aValue > bValue) return currentSort.ascending ? 1 : -1;
                return 0;
            });
            
            // Re-append rows in sorted order
            rows.forEach(row => tbody.appendChild(row));
        }
    </script>
</body>
</html>
'''
    
    return html

def generate_xml(articles):
    """Generate XML file for Power Automate"""
    root = ET.Element('feed')
    root.set('version', '1.0')
    root.set('updated', datetime.now().isoformat())
    
    for article in articles:
        entry = ET.SubElement(root, 'entry')
        
        ET.SubElement(entry, 'source').text = article['source']
        ET.SubElement(entry, 'title').text = article['title']
        ET.SubElement(entry, 'link').text = article['link']
        ET.SubElement(entry, 'published').text = article['published']
        ET.SubElement(entry, 'description').text = article['full_text']
    
    # Pretty print XML
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent='  ')
    return xml_str

def main():
    print("Starting RSS feed processing...")
    print(f"Target: {ARTICLES_PER_SOURCE} latest articles from each source")
    print("-" * 50)
    
    # Process feeds
    articles = process_feeds()
    
    print("-" * 50)
    print(f"Total articles collected: {len(articles)}")
    
    # Generate HTML
    print("Generating HTML...")
    html = generate_html(articles)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✓ HTML saved to {OUTPUT_HTML}")
    
    # Generate XML
    print("Generating XML...")
    xml = generate_xml(articles)
    with open(OUTPUT_XML, 'w', encoding='utf-8') as f:
        f.write(xml)
    print(f"✓ XML saved to {OUTPUT_XML}")
    
    print("-" * 50)
    print("✅ All files generated successfully!")

if __name__ == "__main__":
    main()
