import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom
import time
import json
import os
import google.generativeai as genai
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configure Gemini AI
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
else:
    model = None
    print("Warning: GEMINI_API_KEY not found. LLM features will be disabled.")

# Configuration
FEED_SOURCES = [
    {
        'url': 'https://www.accc.gov.au/rss/news_centre.xml',
        'name': 'ACCC News',
        'type': 'rss'
    },
    {
        'url': 'https://rss.app/feeds/k8R0Dag3ta5LtEQM.xml',
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

ARTICLES_PER_SOURCE = 10  # Fetch 10 latest articles from each source per run
OUTPUT_HTML = 'index.html'
OUTPUT_XML = 'feed-data.xml'
DATABASE_FILE = 'articles-database.json'
DATA_COLLECTION_START_DATE = '22/10/2025'  # Date when we started collecting data

def generate_summary(article_text, title):
    """Generate a summary of the article using Gemini"""
    if not model or not article_text:
        return "Summary not available"
    
    try:
        prompt = f"""Summarize this regulatory media article into 3-5 key bullet points. Focus on the most important information that compliance officers and business executives need to know.

Article Title: {title}

Article Text:
{article_text[:4000]}

Provide only the bullet points, no introduction or conclusion."""

        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating summary: {e}")
        return "Summary generation failed"

def generate_risk_rating(article_text, title, source):
    """Generate risk rating and rationale using Gemini"""
    if not model or not article_text:
        return {"rating": "Not Rated", "rationale": "Risk assessment not available"}
    
    try:
        prompt = f"""You are a senior corporate risk analyst. Your task is to evaluate this media article and assess the level of risk it represents using a 5×5 risk assessment matrix.

Article Title: {title}
Source: {source}

Article Text:
{article_text[:4000]}

FRAMEWORK:
Likelihood (1-5):
1 – Rare: May occur only in exceptional circumstances
2 – Unlikely: Could occur at some time, but not expected
3 – Possible: Might occur occasionally under normal conditions
4 – Likely: Will probably occur
5 – Almost Certain: Expected to occur frequently

Impact (1-5):
1 – Insignificant: Negligible business impact
2 – Minor: Small, contained impact
3 – Moderate: Noticeable effect on operations
4 – Major: Significant financial loss or operational disruption
5 – Catastrophic: Critical business impact threatening viability

Risk Matrix:
- Low: Likelihood 1-2 AND Impact 1-2
- Medium: Likelihood 3 OR Impact 3 (but not both high)
- High: Likelihood 4+ OR Impact 4+ (or both at 3)
- Extreme: Likelihood 5 AND Impact 5, or Likelihood 4-5 with Impact 5

Provide your response in this exact format:
LIKELIHOOD: [number]
IMPACT: [number]
RATING: [Low/Medium/High/Extreme]
RATIONALE: [2-3 sentences explaining the rating based on the article]"""

        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        # Parse the response
        rating = "Not Rated"
        rationale = "Assessment unavailable"
        
        for line in response_text.split('\n'):
            if line.startswith('RATING:'):
                rating = line.replace('RATING:', '').strip()
            elif line.startswith('RATIONALE:'):
                rationale = line.replace('RATIONALE:', '').strip()
        
        # If rationale is still default, try to extract from full response
        if rationale == "Assessment unavailable" and len(response_text) > 50:
            lines = [l for l in response_text.split('\n') if l.strip()]
            if len(lines) >= 4:
                rationale = ' '.join(lines[3:])[:300]
        
        return {"rating": rating, "rationale": rationale}
    
    except Exception as e:
        print(f"Error generating risk rating: {e}")
        return {"rating": "Assessment Failed", "rationale": "Risk assessment could not be completed"}

def generate_industry(article_text, title, source):
    """Generate industry classification and rationale using Gemini"""
    if not model or not article_text:
        return {"industry": "Other", "rationale": "Industry classification not available"}
    
    try:
        prompt = f"""You are a corporate risk and market intelligence analyst. Classify this article into one of the 11 GICS sectors.

Article Title: {title}
Source: {source}

Article Text:
{article_text[:4000]}

GICS SECTORS:
1. Energy
2. Materials
3. Industrials
4. Consumer Discretionary
5. Consumer Staples
6. Health Care
7. Financials
8. Information Technology
9. Communication Services
10. Utilities
11. Real Estate
12. Other (if none apply)

Provide your response in this exact format:
INDUSTRY: [sector name]
RATIONALE: [2-3 sentences explaining why this sector was chosen based on the article content]"""

        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        # Parse the response
        industry = "Other"
        rationale = "Classification unavailable"
        
        for line in response_text.split('\n'):
            if line.startswith('INDUSTRY:'):
                industry = line.replace('INDUSTRY:', '').strip()
            elif line.startswith('RATIONALE:'):
                rationale = line.replace('RATIONALE:', '').strip()
        
        # If rationale is still default, try to extract from full response
        if rationale == "Classification unavailable" and len(response_text) > 50:
            lines = [l for l in response_text.split('\n') if l.strip()]
            if len(lines) >= 2:
                rationale = ' '.join(lines[1:])[:300]
        
        return {"industry": industry, "rationale": rationale}
    
    except Exception as e:
        print(f"Error generating industry classification: {e}")
        return {"industry": "Other", "rationale": "Industry classification could not be completed"}

def load_database():
    """Load existing articles database from JSON file"""
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print(f"Loaded {len(data)} existing articles from database")
                return data
        except Exception as e:
            print(f"Error loading database: {e}")
            return []
    else:
        print("No existing database found, starting fresh")
        return []

def save_database(articles):
    """Save articles database to JSON file"""
    try:
        with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(articles)} articles to database")
    except Exception as e:
        print(f"Error saving database: {e}")

def merge_articles(existing_articles, new_articles):
    """Merge new articles with existing ones, avoiding duplicates"""
    # Create a set of existing article URLs for fast lookup
    existing_urls = {article['link'] for article in existing_articles}
    
    # Track which articles are new
    added_count = 0
    new_article_urls = []
    
    # Add only new articles that aren't already in the database
    for article in new_articles:
        if article['link'] not in existing_urls:
            existing_articles.append(article)
            existing_urls.add(article['link'])
            new_article_urls.append(article['link'])
            added_count += 1
    
    print(f"Added {added_count} new articles to database")
    print(f"Total articles in database: {len(existing_articles)}")
    
    # Sort by date (newest first)
    existing_articles.sort(key=lambda x: parse_date(x['published']), reverse=True)
    
    return existing_articles, new_article_urls

def analyze_articles_with_ai(articles, new_article_urls):
    """Run AI analysis only on articles that don't have it yet (typically new articles)"""
    if not model:
        print("\nWarning: Gemini API not configured. Skipping AI analysis.")
        print("Set GEMINI_API_KEY environment variable to enable AI features.")
        # Set default values for all articles without AI analysis
        for article in articles:
            if not article.get('ai_summary'):
                article['ai_summary'] = "AI analysis not available (API key not configured)"
                article['risk_rating'] = "Not Rated"
                article['risk_rationale'] = "Risk assessment unavailable"
                article['industry'] = "Other"
                article['industry_rationale'] = "Industry classification unavailable"
        return articles
    
    # Find articles that need AI analysis
    articles_to_analyze = [
        article for article in articles 
        if article['link'] in new_article_urls or not article.get('ai_summary')
    ]
    
    if not articles_to_analyze:
        print("\nNo new articles to analyze. All articles already have AI analysis.")
        return articles
    
    print(f"\n{'='*50}")
    print(f"Running AI analysis on {len(articles_to_analyze)} new articles...")
    print(f"{'='*50}")
    
    analyzed_count = 0
    for article in articles_to_analyze:
        # Skip if article doesn't have full text or text is too short
        if not article.get('full_text') or len(article.get('full_text', '')) < 100:
            print(f"  Skipping (insufficient text): {article['title'][:50]}...")
            article['ai_summary'] = "Insufficient article text for analysis"
            article['risk_rating'] = "Not Rated"
            article['risk_rationale'] = "Insufficient text for assessment"
            article['industry'] = "Other"
            article['industry_rationale'] = "Insufficient text for classification"
            continue
        
        try:
            print(f"\n  Analyzing: {article['title'][:60]}...")
            
            # Generate summary
            print("    → Generating summary...")
            article['ai_summary'] = generate_summary(article['full_text'], article['title'])
            time.sleep(1)  # Rate limit
            
            # Generate risk rating
            print("    → Assessing risk...")
            risk_data = generate_risk_rating(article['full_text'], article['title'], article['source'])
            article['risk_rating'] = risk_data['rating']
            article['risk_rationale'] = risk_data['rationale']
            time.sleep(1)  # Rate limit
            
            # Generate industry classification
            print("    → Classifying industry...")
            industry_data = generate_industry(article['full_text'], article['title'], article['source'])
            article['industry'] = industry_data['industry']
            article['industry_rationale'] = industry_data['rationale']
            time.sleep(1)  # Rate limit
            
            analyzed_count += 1
            print(f"    ✓ Complete (Risk: {article['risk_rating']}, Industry: {article['industry']})")
            
        except Exception as e:
            print(f"    ✗ Error during AI analysis: {e}")
            # Set fallback values
            if not article.get('ai_summary'):
                article['ai_summary'] = "Analysis failed"
            if not article.get('risk_rating'):
                article['risk_rating'] = "Assessment Failed"
                article['risk_rationale'] = "Error during assessment"
            if not article.get('industry'):
                article['industry'] = "Other"
                article['industry_rationale'] = "Error during classification"
    
    print(f"\n{'='*50}")
    print(f"✓ AI analysis complete: {analyzed_count} articles analyzed")
    print(f"{'='*50}\n")
    
    return articles

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
    """Process all feeds and extract full text for new articles"""
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
            
            # Initialize AI fields as empty - will be filled later only for new articles
            article['ai_summary'] = None
            article['risk_rating'] = None
            article['risk_rationale'] = None
            article['industry'] = None
            article['industry_rationale'] = None
            
            time.sleep(0.5)  # Be polite, don't hammer servers
        
        all_articles.extend(articles)
    
    # Sort by date (newest first) - keep all articles
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

        .toggle-cell {
            text-align: center;
            padding: 0.5rem !important;
        }

        .toggle-btn {
            background: transparent;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 1rem;
            padding: 0.25rem 0.5rem;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }

        .toggle-btn:hover {
            color: var(--primary);
            transform: scale(1.2);
        }

        .toggle-icon {
            display: inline-block;
            transition: transform 0.3s;
        }

        .toggle-icon.rotated {
            transform: rotate(90deg);
        }

        .full-text-row {
            background: var(--bg-secondary);
        }

        .full-text-row td {
            padding: 0 !important;
        }

        .full-text-row .article-content {
            margin: 0.5rem 1rem 1rem 1rem;
            max-height: 30rem;
        }

        .summary-text {
            color: var(--text-secondary);
            font-size: 0.8125rem;
            line-height: 1.6;
            white-space: pre-line;
        }

        .industry-badge, .risk-badge {
            display: inline-block;
            padding: 0.375rem 0.75rem;
            border-radius: 0.375rem;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.025em;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
        }

        .risk-badge.risk-low {
            background: #14532d;
            color: #86efac;
        }

        .risk-badge.risk-medium {
            background: #713f12;
            color: #fde047;
        }

        .risk-badge.risk-high {
            background: #7c2d12;
            color: #fca5a5;
        }

        .tooltip-container {
            position: relative;
            cursor: help;
        }

        .tooltip {
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-primary);
            color: var(--text-primary);
            padding: 0.75rem 1rem;
            border-radius: 0.5rem;
            font-size: 0.75rem;
            font-weight: 400;
            line-height: 1.5;
            white-space: normal;
            width: 250px;
            box-shadow: var(--shadow-lg);
            border: 1px solid var(--border);
            z-index: 1000;
            pointer-events: none;
            transition: opacity 0.3s, visibility 0.3s;
            text-transform: none;
            letter-spacing: normal;
        }

        .tooltip::after {
            content: '';
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            border: 6px solid transparent;
            border-top-color: var(--bg-primary);
        }

        .tooltip-container:hover .tooltip {
            visibility: visible;
            opacity: 1;
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
            <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                📊 Data collection started: ''' + DATA_COLLECTION_START_DATE + ''' | Total articles: ''' + str(len(articles)) + '''
            </p>
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
                            <th style="width: 3%;">Details</th>
                            <th style="width: 22%;" onclick="sortTable('title')">
                                Title
                                <span class="sort-icon">▼</span>
                            </th>
                            <th style="width: 8%;" onclick="sortTable('date')">
                                Date
                                <span class="sort-icon">▼</span>
                            </th>
                            <th style="width: 25%;">Summary</th>
                            <th style="width: 12%;" onclick="sortTable('industry')">
                                Industry
                                <span class="sort-icon">▼</span>
                            </th>
                            <th style="width: 10%;" onclick="sortTable('risk')">
                                Risk Rating
                                <span class="sort-icon">▼</span>
                            </th>
                        </tr>
                    </thead>
                    <tbody id="articleTableBody">
'''
    
    for article in articles:
        source_class = article['source'].lower().split()[0]
        
        # Escape quotes and special characters for HTML attributes
        risk_rating = article.get('risk_rating', 'Not Rated')
        risk_rationale = article.get('risk_rationale', 'Risk assessment unavailable').replace('"', '&quot;').replace("'", '&#39;')
        industry = article.get('industry', 'Other')
        industry_rationale = article.get('industry_rationale', 'Industry classification unavailable').replace('"', '&quot;').replace("'", '&#39;')
        ai_summary = article.get('ai_summary', 'Summary not available')
        full_text = article.get('full_text', 'Content not available')
        
        # Generate unique ID for this article's collapsible section
        article_id = f"article-{abs(hash(article['link']))}"
        
        # Determine risk color class
        risk_class = 'risk-low'
        if risk_rating.lower() in ['high', 'extreme']:
            risk_class = 'risk-high'
        elif risk_rating.lower() == 'medium':
            risk_class = 'risk-medium'
        
        html += f'''
                        <tr class="article-row" data-source="{article['source']}" data-title="{article['title'].lower()}" data-date="{article['published']}" data-industry="{industry.lower()}" data-risk="{risk_rating.lower()}">
                            <td class="toggle-cell">
                                <button class="toggle-btn" onclick="toggleFullText('{article_id}')" aria-label="Toggle full text">
                                    <span class="toggle-icon" id="{article_id}-icon">▶</span>
                                </button>
                            </td>
                            <td>
                                <span class="source-tag {source_class}">{article['source']}</span>
                                <a href="{article['link']}" target="_blank" class="article-title">{article['title']}</a>
                            </td>
                            <td>
                                <span class="article-date">{format_date(article['published'])}</span>
                            </td>
                            <td>
                                <div class="summary-text">{ai_summary}</div>
                            </td>
                            <td>
                                <span class="industry-badge tooltip-container">
                                    {industry}
                                    <span class="tooltip">{industry_rationale}</span>
                                </span>
                            </td>
                            <td>
                                <span class="risk-badge {risk_class} tooltip-container">
                                    {risk_rating}
                                    <span class="tooltip">{risk_rationale}</span>
                                </span>
                            </td>
                        </tr>
                        <tr class="full-text-row" id="{article_id}" style="display: none;">
                            <td colspan="6">
                                <div class="article-content">{full_text}</div>
                            </td>
                        </tr>
'''
    
    html += '''
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <p>Displaying all collected articles from ACCC, ASIC, APRA, AUSTRAC, and RBA</p>
            <div class="footer-divider"></div>
            <p>Automatically updated every 12 hours • Articles accumulated since ''' + DATA_COLLECTION_START_DATE + '''</p>
        </div>
    </div>

    <script>
        let currentSort = { column: null, ascending: true };

        function toggleFullText(articleId) {
            const fullTextRow = document.getElementById(articleId);
            const icon = document.getElementById(articleId + '-icon');
            
            if (fullTextRow.style.display === 'none') {
                fullTextRow.style.display = 'table-row';
                icon.textContent = '▼';
                icon.classList.add('rotated');
            } else {
                fullTextRow.style.display = 'none';
                icon.textContent = '▶';
                icon.classList.remove('rotated');
            }
        }

        function filterArticles(source) {
            const rows = document.querySelectorAll('.article-row');
            const fullTextRows = document.querySelectorAll('.full-text-row');
            const buttons = document.querySelectorAll('.filter-btn');
            
            buttons.forEach(btn => {
                if (btn.dataset.filter === source) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            
            rows.forEach((row, index) => {
                const rowSource = row.dataset.source;
                const correspondingFullTextRow = fullTextRows[index];
                
                if (source === 'all' || rowSource === source) {
                    row.classList.remove('hidden');
                    // Keep full-text row's display state (might be open or closed)
                } else {
                    row.classList.add('hidden');
                    // Hide corresponding full-text row
                    if (correspondingFullTextRow) {
                        correspondingFullTextRow.classList.add('hidden');
                    }
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
            if (activeHeader) {
                activeHeader.classList.add('sorted');
                const activeIcon = activeHeader.querySelector('.sort-icon');
                activeIcon.textContent = currentSort.ascending ? '▲' : '▼';
            }
            
            // Sort rows
            rows.sort((a, b) => {
                let aValue, bValue;
                
                if (column === 'title') {
                    aValue = a.dataset.title;
                    bValue = b.dataset.title;
                } else if (column === 'date') {
                    aValue = new Date(a.dataset.date || '1970-01-01');
                    bValue = new Date(b.dataset.date || '1970-01-01');
                } else if (column === 'industry') {
                    aValue = a.dataset.industry || 'other';
                    bValue = b.dataset.industry || 'other';
                } else if (column === 'risk') {
                    // Risk rating sorting: Extreme > High > Medium > Low > Not Rated
                    const riskOrder = { 'extreme': 5, 'high': 4, 'medium': 3, 'low': 2, 'not rated': 1, 'assessment failed': 0 };
                    aValue = riskOrder[a.dataset.risk] || 0;
                    bValue = riskOrder[b.dataset.risk] || 0;
                }
                
                if (aValue < bValue) return currentSort.ascending ? -1 : 1;
                if (aValue > bValue) return currentSort.ascending ? 1 : -1;
                return 0;
            });
            
            // Re-append rows with their corresponding full-text rows
            rows.forEach(row => {
                tbody.appendChild(row);
                const articleId = row.querySelector('.toggle-btn').getAttribute('onclick').match(/'(.+)'/)[1];
                const fullTextRow = document.getElementById(articleId);
                if (fullTextRow) {
                    tbody.appendChild(fullTextRow);
                }
            });
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
        
        ET.SubElement(entry, 'source').text = article.get('source', '')
        ET.SubElement(entry, 'title').text = article.get('title', '')
        ET.SubElement(entry, 'link').text = article.get('link', '')
        ET.SubElement(entry, 'published').text = article.get('published', '')
        ET.SubElement(entry, 'summary').text = article.get('ai_summary', '')
        ET.SubElement(entry, 'industry').text = article.get('industry', 'Other')
        ET.SubElement(entry, 'industry_rationale').text = article.get('industry_rationale', '')
        ET.SubElement(entry, 'risk_rating').text = article.get('risk_rating', 'Not Rated')
        ET.SubElement(entry, 'risk_rationale').text = article.get('risk_rationale', '')
        ET.SubElement(entry, 'full_text').text = article.get('full_text', '')
    
    # Pretty print XML
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent='  ')
    return xml_str

def main():
    print("Starting RSS feed processing...")
    print(f"Target: {ARTICLES_PER_SOURCE} latest articles from each source per run")
    print("-" * 50)
    
    # Load existing database
    existing_articles = load_database()
    
    # Process feeds to get new articles
    print("\nFetching new articles...")
    new_articles = process_feeds()
    
    print("-" * 50)
    print(f"New articles fetched: {len(new_articles)}")
    
    # Merge with existing articles
    print("\nMerging with existing database...")
    all_articles, new_article_urls = merge_articles(existing_articles, new_articles)
    
    # Run AI analysis ONLY on new articles that don't have analysis yet
    if new_article_urls:
        all_articles = analyze_articles_with_ai(all_articles, new_article_urls)
    else:
        print("\nNo new articles to analyze.")
    
    # Save updated database
    print("\nSaving database...")
    save_database(all_articles)
    
    print("-" * 50)
    print(f"Total articles in database: {len(all_articles)}")
    
    # Generate HTML
    print("\nGenerating HTML...")
    html = generate_html(all_articles)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✓ HTML saved to {OUTPUT_HTML}")
    
    # Generate XML
    print("Generating XML...")
    xml = generate_xml(all_articles)
    with open(OUTPUT_XML, 'w', encoding='utf-8') as f:
        f.write(xml)
    print(f"✓ XML saved to {OUTPUT_XML}")
    
    print("-" * 50)
    print("✅ All files generated successfully!")
    print(f"Database contains {len(all_articles)} total articles")

if __name__ == "__main__":
    main()
