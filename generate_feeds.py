import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom
import time
import json
import os
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import google.generativeai as genai
import re
import html

# Suppress Google Cloud warnings (optional - removes harmless ALTS warning)
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GLOG_minloglevel'] = '2'

# Configure Google Gemini API
# IMPORTANT: Set GEMINI_API_KEY as an environment variable
# 
# For local development:
#   export GEMINI_API_KEY="your_api_key_here"
# 
# For GitHub Actions:
#   Add GEMINI_API_KEY as a repository secret (see setup instructions)
#
# Get your free API key from: https://aistudio.google.com/app/apikey
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = "gemini-2.0-flash"  # Gemini 2.0 Flash (experimental, free tier available)

# Rate limiting: Gemini free tier has generous limits
# 15 requests per minute, 1500 requests per day for free tier
RATE_LIMIT_DELAY = 4.5  # seconds between API calls (13 requests per minute to be safe)

# Initialize Gemini
gemini_available = bool(GEMINI_API_KEY)
if gemini_available:
    genai.configure(api_key=GEMINI_API_KEY)
    print(f"âœ“ Using Google Gemini API ({GEMINI_MODEL}) for LLM features")
    print(f"âœ“ Rate limit: {int(60/RATE_LIMIT_DELAY)} requests per minute")
else:
    print("=" * 60)
    print("ERROR: GEMINI_API_KEY not found!")
    print("=" * 60)
    print("Set the environment variable:")
    print("  Linux/Mac:  export GEMINI_API_KEY='your_api_key'")
    print("  Windows:    set GEMINI_API_KEY=your_api_key")
    print("")
    print("Get your free API key from:")
    print("  https://aistudio.google.com/app/apikey")
    print("=" * 60)

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

ARTICLES_PER_SOURCE = 10  # Fetch 10 latest articles from each source per run
OUTPUT_HTML = 'index.html'
OUTPUT_XML = 'feed-data.xml'
DATABASE_FILE = 'articles-database.json'
DATA_COLLECTION_START_DATE = '22/10/2025'  # Date when we started collecting data

def call_gemini_api(prompt, max_retries=3):
    """Call Google Gemini API using official SDK with retry logic and rate limiting"""
    if not gemini_available:
        return None
    
    for attempt in range(max_retries):
        try:
            # Initialize model with generation config
            model = genai.GenerativeModel(
                model_name=GEMINI_MODEL,
                generation_config={
                    "temperature": 0.7,
                    "top_k": 40,
                    "top_p": 0.95,
                    "max_output_tokens": 8192,
                }
            )
            
            # Generate content (much simpler with SDK!)
            response = model.generate_content(prompt)
            
            # Extract text from response
            if response.text:
                return response.text.strip()
            
            print(f"  Empty response from Gemini API")
            return None
            
        except Exception as e:
            error_msg = str(e).lower()
            print(f"  Gemini API error (attempt {attempt + 1}/{max_retries}): {e}")
            
            # Handle rate limiting
            if '429' in error_msg or 'quota' in error_msg or 'rate limit' in error_msg:
                print(f"  Rate limit exceeded. Waiting longer before retry...")
                time.sleep(10)
            elif attempt < max_retries - 1:
                time.sleep(2)
            else:
                return None
    
    return None

def clean_llm_output(text):
    """Clean and format LLM output by removing extra formatting"""
    if not text:
        return text
    
    lines = text.split('\n')
    filtered_lines = []
    
    for line in lines:
        # Skip lines that are just separators or whitespace
        stripped = line.strip()
        if stripped in ['---', '--', '____', '___', '**', '***', '====', '===', '==', '']:
            continue
        filtered_lines.append(line)
    
    # Join and clean up multiple consecutive blank lines
    result = '\n'.join(filtered_lines)
    
    # Remove multiple consecutive newlines (more than 2)
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    # Remove trailing separators and whitespace
    result = result.strip()
    
    # Remove trailing dashes at the end (like "-- " or "--" or "---")
    result = re.sub(r'[-_=*]+\s*$', '', result)
    
    # Final strip to remove any remaining trailing whitespace
    result = result.strip()
    
    return result

def generate_summary(article_text, title):
    """Generate a summary of the article using LLM API"""
    if not gemini_available or not article_text:
        return "Summary not available"
    
    try:
        prompt = f"""Summarize the article title and text into 3-5 key bullets. Also provide a short introduction before the bullets to provide a summary of the whole article. Focus on the most important information which compliance officers and business executives need to know. Avoid opinions or speculation.

Article Title: {title}

Article Text:
{article_text[:15000]}

"""

        response_text = call_gemini_api(prompt)
        
        # Rate limiting: wait between API calls
        time.sleep(RATE_LIMIT_DELAY)
        
        if response_text:
            # Remove any promotional content
            response_text = clean_llm_output(response_text)
            return response_text
        else:
            return "Summary generation failed"
    except Exception as e:
        print(f"Error generating summary: {e}")
        return "Summary generation failed"


def generate_risk_rating(article_text, title, source):
    """Generate impact rating and rationale using LLM API"""
    if not gemini_available or not article_text:
        return {"rating": "Not Rated", "rationale": "Impact assessment not available", "confidence": "N/A"}
    
    try:
        prompt = f"""You are an expert regulatory analyst specializing in assessing the potential impact of government and regulatory media releases.

Article Title: {title}
Source: {source}

Article Text:
{article_text[:15000]}

Your tasks:
1.Read the full article title and text.
2.Assign a regulatory impact rating (1 to 5).
3.Give a concise rationale citing text evidence, linking it to scale thresholds, and noting assumptions or uncertainties.
4.State your confidence level (High/Medium/Low) based on clarity and completeness of the release.

Impact Scale (1 to 5):
1 Minimal: Informational only; no new requirements.
2 Low: Minor procedural/reporting updates; limited cost/scope.
3 Moderate: Noticeable process/system changes; moderate resources.
4 High: Major operational/financial changes; significant compliance risk.
5 Critical: Fundamental business/regulatory shifts; high cost/risk; board-level attention.

Consider: scope, depth, timing, compliance risk, strategic significance.

Provide your response in this exact format:
IMPACT: [1, 2, 3, 4, or 5]
CONFIDENCE: [High, Medium, or Low]
RATIONALE: [3-5 sentences, max 120 words, with evidence from the text]"""

        response_text = call_gemini_api(prompt)
        
        # Rate limiting: wait between API calls
        time.sleep(RATE_LIMIT_DELAY)
        
        if not response_text:
            return {"rating": "Assessment Failed", "rationale": "Impact assessment could not be completed", "confidence": "N/A"}
        
        # Remove any promotional content
        response_text = clean_llm_output(response_text)
        
        # Parse the response
        impact_score = None
        confidence = "N/A"
        rationale = "Assessment unavailable"
        
        for line in response_text.split('\n'):
            if line.startswith('IMPACT:'):
                impact_str = line.replace('IMPACT:', '').strip()
                try:
                    impact_score = int(impact_str)
                except ValueError:
                    pass
            elif line.startswith('CONFIDENCE:'):
                confidence_str = line.replace('CONFIDENCE:', '').strip().lower()
                # Map various confidence inputs to High/Medium/Low
                if confidence_str in ['high', '4', '5', 'very high']:
                    confidence = "High"
                elif confidence_str in ['medium', 'moderate', '3']:
                    confidence = "Medium"
                elif confidence_str in ['low', '1', '2', 'very low']:
                    confidence = "Low"
                else:
                    confidence = confidence_str.title() if confidence_str else "N/A"
            elif line.startswith('RATIONALE:'):
                rationale = line.replace('RATIONALE:', '').strip()
        
        # Map impact score to rating label
        if impact_score == 1:
            rating = "Minimal"
        elif impact_score == 2:
            rating = "Low"
        elif impact_score == 3:
            rating = "Moderate"
        elif impact_score == 4:
            rating = "High"
        elif impact_score == 5:
            rating = "Critical"
        else:
            rating = "Not Rated"
        
        # If rationale is still default, try to extract from full response
        if rationale == "Assessment unavailable" and len(response_text) > 50:
            lines = [l for l in response_text.split('\n') if l.strip()]
            if len(lines) >= 3:
                rationale = ' '.join(lines[2:])[:300]
        
        return {"rating": rating, "rationale": rationale, "confidence": confidence}
    
    except Exception as e:
        print(f"Error generating impact rating: {e}")
        return {"rating": "Assessment Failed", "rationale": "Impact assessment could not be completed", "confidence": "N/A"}

def generate_industry(article_text, title, source):
    """Generate industry classification(s), rationale, and confidence using LLM API"""
    if not gemini_available or not article_text:
        return {"industries": ["Other"], "rationale": "Industry classification not available", "confidence": "N/A"}
    
    try:
        prompt = f"""You are an expert in financial markets, regulatory policy, and industry classification using the Global Industry Classification Standard (GICS). Your task is to analyze a regulatory media release and classify which industry group(s) the release most directly impacts, based on the 25 GICS Industry Groups. You must also provide a clear rationale explaining why the release impacts those industries, referring to keywords, subject matter, entities involved, or regulatory implications.

Article Title: {title}
Source: {source}

Article Text:
{article_text[:15000]}

If the release does not clearly relate to any specific industry, or is too broad to assign, select "Other." If the release clearly applies to all industries, select "All."
Important: If "Other" or "All" is selected, do not assign any other industry group.

Output Format (follow exactly):
Industries Impacted: [List of relevant GICS Industry Groups - comma-separated, multiple allowed except when "Other" or "All" is selected]
Rationale: [Brief explanation of why these industries are impacted, 2-4 sentences]
Confidence: [High / Medium / Low - depending on the clarity and strength of the relationship]"""

        response_text = call_gemini_api(prompt)
        
        # Rate limiting: wait between API calls
        time.sleep(RATE_LIMIT_DELAY)
        
        if not response_text:
            return {"industries": ["Other"], "rationale": "Industry classification could not be completed", "confidence": "N/A"}
        
        # Remove any promotional content
        response_text = clean_llm_output(response_text)
        
        # Parse the response
        industries = []
        rationale = "Classification unavailable"
        confidence = "N/A"
        
        for line in response_text.split('\n'):
            if line.startswith('Industries Impacted:'):
                industries_str = line.replace('Industries Impacted:', '').strip()
                # Split by comma and clean up each industry name
                industries = [ind.strip() for ind in industries_str.split(',') if ind.strip()]
            elif line.startswith('Rationale:'):
                rationale = line.replace('Rationale:', '').strip()
            elif line.startswith('Confidence:'):
                confidence = line.replace('Confidence:', '').strip()
        
        # Fallback: if no industries parsed, set to Other
        if not industries:
            industries = ["Other"]
        
        # If rationale is still default, try to extract from full response
        if rationale == "Classification unavailable" and len(response_text) > 50:
            lines = [l for l in response_text.split('\n') if l.strip()]
            if len(lines) >= 2:
                rationale = ' '.join(lines[1:])[:300]
        
        return {"industries": industries, "rationale": rationale, "confidence": confidence}
    
    except Exception as e:
        print(f"Error generating industry classification: {e}")
        return {"industries": ["Other"], "rationale": "Industry classification could not be completed", "confidence": "N/A"}

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

def cleanup_html_from_database(articles):
    """Remove full_text_html and convert any HTML in full_text to plain text (no paragraph breaks)"""
    from bs4 import BeautifulSoup
    
    cleaned_count = 0
    for article in articles:
        # Remove full_text_html field if it exists
        if 'full_text_html' in article:
            del article['full_text_html']
            cleaned_count += 1
        
        # Get current full_text
        full_text = article.get('full_text', '')
        
        if not full_text:
            continue
            
        # If full_text contains HTML tags, strip them and convert to plain text
        if '<' in full_text and '>' in full_text:
            # Parse HTML and extract plain text
            soup = BeautifulSoup(full_text, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            lines = [line for line in lines if len(line) > 15]
            article['full_text'] = ' '.join(lines)  # Join with single space, no line breaks
        
        # Always remove any newlines/paragraph breaks from the final text
        # This catches both HTML-cleaned text and plain text with newlines
        if '\n' in article['full_text']:
            # Replace all newlines with spaces and normalize whitespace
            article['full_text'] = ' '.join(article['full_text'].split())
    
    if cleaned_count > 0:
        print(f"Cleaned HTML formatting from {cleaned_count} articles")
    
    return articles

def save_database(articles):
    """Save articles database to JSON file (excluding full_text_html)"""
    try:
        # Final cleanup: ensure no full_text_html fields are saved
        cleaned_articles = []
        for article in articles:
            cleaned_article = {k: v for k, v in article.items() if k != 'full_text_html'}
            cleaned_articles.append(cleaned_article)
        
        with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cleaned_articles, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(cleaned_articles)} articles to database")
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
    if not gemini_available:
        print("\nWarning: Google Gemini API not available. Skipping AI analysis.")
        print("Set GEMINI_API_KEY environment variable or add it to the script.")
        print("Get your free API key from: https://aistudio.google.com/app/apikey")
        # Set default values for all articles without AI analysis
        for article in articles:
            if not article.get('ai_summary'):
                article['ai_summary'] = "AI analysis not available"
                article['risk_rating'] = "Not Rated"
                article['risk_rationale'] = "Risk assessment unavailable"
                article['risk_confidence'] = "N/A"
                article['industries'] = ["Other"]
                article['industry'] = "Other"
                article['industry_rationale'] = "Industry classification unavailable"
                article['industry_confidence'] = "N/A"
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
            article['risk_confidence'] = "N/A"
            article['industries'] = ["Other"]
            article['industry'] = "Other"
            article['industry_rationale'] = "Insufficient text for classification"
            article['industry_confidence'] = "N/A"
            continue
        
        try:
            print(f"\n  Analyzing: {article['title'][:60]}...")
            
            # Generate short summary
            print("    -> Generating summary...")
            article['ai_summary'] = generate_summary(article['full_text'], article['title'])
            
            # Generate risk rating using full article text
            print("    -> Assessing impact...")
            risk_data = generate_risk_rating(article['full_text'], article['title'], article['source'])
            article['risk_rating'] = risk_data['rating']
            article['risk_rationale'] = risk_data['rationale']
            article['risk_confidence'] = risk_data.get('confidence', 'N/A')
            
            # Generate industry classification using full article text
            print("    -> Classifying industry...")
            industry_data = generate_industry(article['full_text'], article['title'], article['source'])
            article['industries'] = industry_data['industries']  # Now an array
            article['industry_rationale'] = industry_data['rationale']
            article['industry_confidence'] = industry_data.get('confidence', 'N/A')
            
            # For backward compatibility, also store first industry as 'industry'
            article['industry'] = industry_data['industries'][0] if industry_data['industries'] else 'Other'
            
            analyzed_count += 1
            industries_str = ', '.join(article['industries'][:3])  # Show first 3
            if len(article['industries']) > 3:
                industries_str += f' (+{len(article["industries"])-3} more)'
            print(f"    [OK] Complete (Impact: {article['risk_rating']}, Industries: {industries_str})")
            
        except Exception as e:
            print(f"    [ERROR] Error during AI analysis: {e}")
            # Set fallback values
            if not article.get('ai_summary'):
                article['ai_summary'] = "Analysis failed"
            if not article.get('risk_rating'):
                article['risk_rating'] = "Assessment Failed"
                article['risk_rationale'] = "Error during assessment"
                article['risk_confidence'] = "N/A"
            if not article.get('industries'):
                article['industries'] = ["Other"]
                article['industry'] = "Other"
                article['industry_rationale'] = "Error during classification"
                article['industry_confidence'] = "N/A"
    
    print(f"\n{'='*50}")
    print(f"[OK] AI analysis complete: {analyzed_count} articles analyzed")
    print(f"{'='*50}\n")
    
    return articles

def extract_date_from_text(text):
    """Extract the FIRST date from text content"""
    
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
            except ValueError:
                continue
    
    return None

def fetch_full_text(url, retry_count=0, max_retries=2):
    """Fetch and extract full text from article URL with browser mimicry"""
    try:
        # More realistic modern browser headers (Chrome 131)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            # Remove Accept-Encoding to get uncompressed response (AUSTRAC has compression issues)
            # 'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
        }
        
        session = requests.Session()
        session.headers.update(headers)
        
        # Add referer for AUSTRAC to look more natural
        if 'austrac.gov.au' in url:
            session.headers['Referer'] = 'https://www.austrac.gov.au/media-release'
        
        response = session.get(url, timeout=60, allow_redirects=True)
        response.raise_for_status()
        
        # Debug: Check encoding issues for AUSTRAC
        if 'austrac.gov.au' in url:
            print(f"    DEBUG: Response URL: {response.url}")
            print(f"    DEBUG: Response encoding: {response.encoding}")
            print(f"    DEBUG: Response apparent encoding: {response.apparent_encoding}")
            print(f"    DEBUG: Content-Encoding header: {response.headers.get('Content-Encoding', 'none')}")
            print(f"    DEBUG: Response length: {len(response.content)} bytes (raw), {len(response.text)} chars (decoded)")
            print(f"    DEBUG: Response status: {response.status_code}")
            
            # Check if response looks compressed (starts with gzip magic bytes)
            if response.content[:2] == b'\x1f\x8b':
                print(f"    WARNING: Response is gzipped but not decompressed!")
                try:
                    import gzip
                    decompressed = gzip.decompress(response.content)
                    response._content = decompressed
                    print(f"    Manually decompressed: {len(decompressed)} bytes")
                except Exception as e:
                    print(f"    ERROR decompressing: {e}")
            
            # Show first 500 chars to verify it's readable
            print(f"    DEBUG: First 500 chars of HTML:")
            print(response.text[:500])
            print(f"    DEBUG: ...")
        
        import random
        time.sleep(random.uniform(0.5, 1.5))
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Debug: Show what HTML elements exist for AUSTRAC
        if 'austrac.gov.au' in url:
            print(f"    DEBUG: Page title: {soup.title.string if soup.title else 'No title'}")
            
            # Get all HTML elements to see what exists
            all_elements = soup.find_all()
            element_types = {}
            for elem in all_elements:
                element_types[elem.name] = element_types.get(elem.name, 0) + 1
            
            print(f"    DEBUG: Total HTML elements found: {len(all_elements)}")
            print(f"    DEBUG: Element types: {dict(list(element_types.items())[:10])}")
            
            # Check specific elements
            all_divs = soup.find_all('div')
            all_ps = soup.find_all('p')
            all_spans = soup.find_all('span')
            print(f"    DEBUG: Found {len(all_divs)} divs, {len(all_ps)} paragraphs, {len(all_spans)} spans")
            
            # Check if there's a body tag and what's in it
            body = soup.find('body')
            if body:
                body_text = body.get_text(strip=True)
                print(f"    DEBUG: Body tag exists with {len(body_text)} chars of text")
                if len(body_text) > 0:
                    print(f"    DEBUG: First 300 chars of body text: {body_text[:300]}")
            else:
                print(f"    DEBUG: No body tag found!")
        
        # Extract date for ASIC articles (they have date on the article page)
        extracted_date = None
        if 'asic.gov.au' in url:
            # Look for <time class="nh-mr-date">
            date_elem = soup.select_one('time.nh-mr-date')
            if date_elem:
                extracted_date = date_elem.get_text(strip=True)
                print(f"    Found ASIC date: {extracted_date}")
            else:
                # Try alternative selectors
                date_elem = soup.select_one('time, .date, .published, [class*="date"]')
                if date_elem:
                    extracted_date = date_elem.get_text(strip=True)
                    print(f"    Found ASIC date (alternative): {extracted_date}")
        
        for script in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            script.decompose()
        
        content = None
        found_selector = None
        
        # AUSTRAC-specific selectors first (they have a unique structure)
        if 'austrac.gov.au' in url:
            austrac_selectors = [
                '.field--name-body',  # Main content field in Drupal
                '.field--type-text-with-summary',
                'article .content',
                '.node__content',
                '.media-release-content',
                '.field-item',
            ]
            
            for selector in austrac_selectors:
                content = soup.select_one(selector)
                if content and len(content.get_text(strip=True)) > 200:
                    found_selector = selector
                    print(f"    Found AUSTRAC content with selector: {selector} ({len(content.get_text(strip=True))} chars)")
                    break
        
        # General selectors if no content found yet
        if not content or len(content.get_text(strip=True)) < 200:
            selectors = [
                'article', 
                '.article-content', 
                '.content', 
                'main', 
                '.main-content', 
                '.post-content', 
                '.entry-content', 
                '#content', 
                '.article-body', 
                '[role="main"]', 
                '.media-release', 
                '.news-content', 
                '.publication-content',
                '.page-content',
                '#main-content',
            ]
            
            for selector in selectors:
                content = soup.select_one(selector)
                if content and len(content.get_text(strip=True)) > 200:
                    found_selector = selector
                    print(f"    Found content with selector: {selector} ({len(content.get_text(strip=True))} chars)")
                    break
        
        # If still no content, try to find the largest text block
        if not content or len(content.get_text(strip=True)) < 200:
            print(f"    Standard selectors failed, searching for largest text block...")
            
            # For debugging AUSTRAC issues, save the HTML
            if 'austrac.gov.au' in url and not content:
                try:
                    debug_file = '/tmp/austrac_debug.html'
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    print(f"    DEBUG: Saved HTML to {debug_file} for inspection")
                except (IOError, OSError):
                    pass
            
            all_divs = soup.find_all(['div', 'section', 'article', 'main'])
            max_length = 0
            best_div = None
            
            for div in all_divs:
                # Skip if it has navigation-like classes
                div_classes = ' '.join(div.get('class', [])).lower()
                div_id = (div.get('id') or '').lower()
                
                # Skip navigation, menus, sidebars, etc.
                skip_keywords = ['nav', 'menu', 'sidebar', 'footer', 'header', 'cookie', 'popup', 'modal']
                if any(skip in div_classes or skip in div_id for skip in skip_keywords):
                    continue
                
                text = div.get_text(strip=True)
                if len(text) > max_length and len(text) > 200:
                    max_length = len(text)
                    best_div = div
                    if 'austrac.gov.au' in url and max_length > 500:
                        # Show what we found for debugging
                        div_info = f"class={div.get('class')} id={div.get('id')}"
                        print(f"    DEBUG: Found candidate div ({max_length} chars): {div_info}")
            
            if best_div:
                content = best_div
                found_selector = 'largest-text-block'
                print(f"    Found content via largest text block: {max_length} chars")
        
        if not content:
            content = soup.find('body')
            if content:
                found_selector = 'body'
                print(f"    Fallback to body tag ({len(content.get_text(strip=True))} chars)")
        
        if content:
            # Extract PLAIN TEXT version only (no HTML, no paragraph breaks)
            text = content.get_text(separator=' ', strip=True)
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            lines = [line for line in lines if len(line) > 15]
            full_text_plain = ' '.join(lines)  # Join with single space
            
            if len(full_text_plain) < 100:
                print(f"    WARNING: Content too short ({len(full_text_plain)} chars), might not be main content")
                return {
                    'plain': "Insufficient content extracted",
                    'date': extracted_date
                }
            
            print(f"    Extracted {len(full_text_plain)} characters")
            return {
                'plain': full_text_plain[:30000],  # Limit to 15000 chars
                'date': extracted_date
            }
        
        print(f"    ERROR: No content found on page")
        return {
            'plain': "Content not available",
            'date': extracted_date
        }
        
    except requests.exceptions.Timeout as e:
        if retry_count < max_retries:
            wait_time = (retry_count + 1) * 2
            print(f"  Timeout on attempt {retry_count + 1}/{max_retries + 1}, retrying in {wait_time}s...")
            time.sleep(wait_time)
            return fetch_full_text(url, retry_count + 1, max_retries)
        else:
            print(f"  Max retries reached for {url}")
            error_msg = f"Error: Timeout after {max_retries + 1} attempts"
            return {'plain': error_msg, 'date': None}
            
    except requests.exceptions.RequestException as e:
        print(f"  Network error fetching full text from {url}: {e}")
        error_msg = f"Error fetching content: {str(e)}"
        return {'plain': error_msg, 'date': None}
        
    except Exception as e:
        print(f"  Unexpected error fetching full text from {url}: {e}")
        error_msg = f"Error fetching content: {str(e)}"
        return {'plain': error_msg, 'date': None}


def fetch_asic_news_selenium():
    """Scrape ASIC media releases page using Selenium (JavaScript required)"""
    driver = None
    try:
        # Set up Chrome options for headless mode (optimized for CI environments)
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')  # Use new headless mode
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-logging')
        chrome_options.add_argument('--log-level=3')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # Additional stability options for CI
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        print(f"  Initializing Chrome WebDriver...")
        # Initialize the driver
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(90)  # Increased timeout for CI environments
        
        url = 'https://www.asic.gov.au/newsroom/media-releases/'
        print(f"  Loading {url} with Selenium...")
        driver.get(url)
        
        # Wait for the nr-list to be present (increased timeout for CI)
        print("  Waiting for page to load...")
        wait = WebDriverWait(driver, 45)  # Increased from 30 to 45 seconds
        nr_list = wait.until(EC.presence_of_element_located((By.ID, "nr-list")))
        
        # Additional wait for JavaScript to fully render (critical for CI)
        print("  Waiting for JavaScript to render...")
        time.sleep(3)  # Increased from 2 to 3 seconds
        
        # Verify content is actually loaded
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#nr-list li")) > 0)
        
        # Get the page source and parse with BeautifulSoup
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Find the ul#nr-list
        nr_list = soup.find('ul', id='nr-list')
        
        if not nr_list:
            print("  ERROR: Could not find ul#nr-list even after JavaScript rendering")
            print(f"  Page title: {driver.title}")
            return []
        
        articles = []
        news_items = nr_list.find_all('li', recursive=False)
        
        print(f"  Found {len(news_items)} ASIC items in ul#nr-list")
        
        # Process up to configured number of items
        for item in news_items[:ARTICLES_PER_SOURCE]:
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
                
                # Try to find date - ASIC has <p class="nr-date">
                date_text = ''
                date_elem = item.find('p', class_='nr-date')
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                    print(f"    Found date: {date_text}")
                
                # Fallback: try to find in div.nh-list-info
                if not date_text:
                    info_div = item.find('div', class_='nh-list-info')
                    if info_div:
                        date_text = extract_date_from_text(info_div.get_text())
                
                # Last resort: extract from full item text
                if not date_text:
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
        print(f"\n{'='*60}")
        print(f"ERROR fetching ASIC news with Selenium: {e}")
        print(f"{'='*60}")
        import traceback
        traceback.print_exc()
        
        # Provide debugging info if driver was initialized
        if driver:
            try:
                print(f"Current URL: {driver.current_url}")
                print(f"Page title: {driver.title}")
                print(f"Page source length: {len(driver.page_source)}")
            except Exception:
                print("Could not retrieve driver debug info")
        
        print(f"{'='*60}\n")
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass  # Ignore cleanup errors

def fetch_apra_news():
    """Scrape APRA news page to create RSS-like entries with browser mimicry"""
    try:
        url = 'https://www.apra.gov.au/news-and-publications'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,en-AU;q=0.8',
            # Remove Accept-Encoding to avoid compression issues
            # 'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
        
        print(f"  Fetching APRA page...")
        response = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
        response.raise_for_status()
        
        print(f"  DEBUG: Response status: {response.status_code}")
        print(f"  DEBUG: Response URL: {response.url}")
        print(f"  DEBUG: Response length: {len(response.text)} chars")
        
        # Check if response looks compressed
        if response.content[:2] == b'\x1f\x8b':
            print(f"  WARNING: Response is gzipped, attempting to decompress...")
            try:
                import gzip
                decompressed = gzip.decompress(response.content)
                response._content = decompressed
                print(f"  Manually decompressed: {len(decompressed)} bytes")
            except Exception as e:
                print(f"  ERROR decompressing: {e}")
        
        soup = BeautifulSoup(response.text, 'html.parser')  # Use .text instead of .content
        
        print(f"  DEBUG: Page title: {soup.title.string if soup.title else 'No title'}")
        
        # Show first 800 chars to see structure
        print(f"  DEBUG: First 800 chars of HTML:")
        print(response.text[:800])
        print(f"  ...")
        
        # Count elements
        all_divs = soup.find_all('div')
        all_articles = soup.find_all('article')
        all_links = soup.find_all('a')
        print(f"  DEBUG: Found {len(all_divs)} divs, {len(all_articles)} articles, {len(all_links)} links")
        
        articles = []
        
        # Try multiple selectors for APRA's page structure
        news_items = soup.select('.view-news-publications .views-row')
        print(f"  Trying: .view-news-publications .views-row -> {len(news_items)} items")
        if not news_items:
            news_items = soup.select('.view-content .views-row')
            print(f"  Trying: .view-content .views-row -> {len(news_items)} items")
        if not news_items:
            news_items = soup.select('article')
            print(f"  Trying: article -> {len(news_items)} items")
        if not news_items:
            news_items = soup.select('.views-row')
            print(f"  Trying: .views-row -> {len(news_items)} items")
        if not news_items:
            # Show what div classes exist
            div_classes = set()
            for div in all_divs[:100]:
                classes = div.get('class', [])
                div_classes.update(classes)
            print(f"  DEBUG: Sample div classes found on page: {sorted(list(div_classes))[:30]}")
            
            all_links = soup.find_all('a', href=True)
            news_items = [link.parent for link in all_links if '/news' in link.get('href', '') or '/publication' in link.get('href', '')]
            print(f"  Last resort (/news or /publication links): {len(news_items)} items")
        
        print(f"  Found {len(news_items)} potential APRA items")
        
        for item in news_items[:ARTICLES_PER_SOURCE * 2]:
            try:
                title_elem = item.select_one('h3 a, h2 a, h4 a, .title a, a[href*="/news"], a[href*="/publication"]')
                
                if not title_elem:
                    links = item.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        if '/news' in href or '/publication' in href:
                            title_elem = link
                            break
                
                if not title_elem or not title_elem.get_text(strip=True):
                    continue
                
                article_url = title_elem.get('href', '')
                if article_url.startswith('/'):
                    article_url = 'https://www.apra.gov.au' + article_url
                
                if not article_url or 'apra.gov.au' not in article_url:
                    continue
                
                title_text = title_elem.get_text(strip=True)
                if len(title_text) < 10 or title_text.lower() in ['home', 'news', 'publications', 'media']:
                    continue
                
                date_elem = item.select_one('.date, time, .views-field-created, .field--name-created, .views-field-field-date, .field--name-field-date, [class*="date"]')
                date_text = ''
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                if not date_text:
                    item_text = item.get_text()
                    date_text = extract_date_from_text(item_text)
                
                summary_elem = item.select_one('.summary, .views-field-body, .field--name-body, .field--type-text-long, p')
                summary_text = summary_elem.get_text(strip=True)[:300] if summary_elem else ''
                
                article = {
                    'title': title_text,
                    'link': article_url,
                    'published': date_text or '',
                    'summary': summary_text,
                }
                articles.append(article)
                print(f"    Found APRA article: {title_text[:50]}...")
                
                if len(articles) >= ARTICLES_PER_SOURCE:
                    break
                    
            except Exception as e:
                print(f"  Error parsing APRA item: {e}")
                continue
        
        print(f"  Successfully parsed {len(articles)} APRA articles")
        return articles
        
    except Exception as e:
        print(f"  ERROR fetching APRA news: {e}")
        import traceback
        traceback.print_exc()
        return []

def fetch_rba_news():
    """Scrape RBA media releases page to create RSS-like entries with browser mimicry"""
    try:
        url = 'https://www.rba.gov.au/media-releases/'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,en-AU;q=0.8',
            # Remove Accept-Encoding to avoid compression issues
            # 'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
        response = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')  # Use .text for proper encoding
        articles = []
        
        # RBA uses <ul class="list-articles rss-mr-list">
        media_releases_list = soup.select_one('ul.list-articles.rss-mr-list')
        
        if not media_releases_list:
            print("  ERROR: Could not find ul.list-articles.rss-mr-list container")
            return []
        
        # Find all list items with class="item rss-mr-item"
        news_items = media_releases_list.select('li.item.rss-mr-item')
        
        print(f"  Found {len(news_items)} potential RBA items in list-articles")
        
        # Limit to configured number of articles
        for item in news_items[:ARTICLES_PER_SOURCE]:
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
                
                # Try to find date in the item - RBA uses <span class="date" itemprop="datePublished">
                date_elem = item.select_one('span.date[itemprop="datePublished"]')
                date_text = ''
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                    print(f"    Found date element: {date_text}")
                
                # Fallback to broader selectors if specific one fails
                if not date_text:
                    date_elem = item.select_one('span.date, .date, time')
                    if date_elem and date_elem != title_elem:
                        date_text = date_elem.get_text(strip=True)
                        print(f"    Found date element (fallback): {date_text}")
                
                # Last resort: try to extract from item text
                if not date_text:
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
    """Parse RSS feed and extract entries with retry logic and browser mimicry"""
    max_attempts = 3
    timeout = 60  # Increased to 60 seconds
    
    for attempt in range(max_attempts):
        try:
            # Comprehensive browser-like headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/rss+xml,application/xml;q=0.9,application/xhtml+xml,text/html;q=0.8,*/*;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9,en-AU;q=0.8',
                # Remove Accept-Encoding to avoid compression issues (like AUSTRAC)
                # 'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0'
            }
            
            print(f"  Attempt {attempt + 1}/{max_attempts} to fetch RSS feed...")
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            
            # Debug for AUSTRAC - check response
            if 'austrac.gov.au' in url:
                print(f"  DEBUG: Response status: {response.status_code}")
                print(f"  DEBUG: Response URL: {response.url}")
                print(f"  DEBUG: Content-Type: {response.headers.get('Content-Type', 'unknown')}")
                print(f"  DEBUG: Content-Encoding: {response.headers.get('Content-Encoding', 'none')}")
                print(f"  DEBUG: Response length: {len(response.content)} bytes")
                
                # Check if response is gzipped but not decompressed
                if response.content[:2] == b'\x1f\x8b':
                    print(f"  WARNING: Response is gzipped but not automatically decompressed!")
                    try:
                        import gzip
                        decompressed = gzip.decompress(response.content)
                        response._content = decompressed
                        print(f"  Manually decompressed: {len(decompressed)} bytes")
                    except Exception as e:
                        print(f"  ERROR decompressing: {e}")
                
                print(f"  DEBUG: First 500 chars of response:")
                print(response.text[:500])
                print(f"  DEBUG: ...")
            
            # Parse the fetched content
            feed = feedparser.parse(response.content)
            
            # Debug logging for AUSTRAC
            if 'austrac.gov.au' in url:
                print(f"  DEBUG: Feed title: {feed.feed.get('title', 'No title')}")
                print(f"  DEBUG: Feed entries count: {len(feed.entries)}")
                print(f"  DEBUG: Feed bozo (parse error): {feed.bozo}")
                if feed.bozo:
                    print(f"  DEBUG: Parse exception: {feed.bozo_exception}")
                if len(feed.entries) > 0:
                    print(f"  DEBUG: First entry keys: {feed.entries[0].keys()}")
                    print(f"  DEBUG: First entry: {feed.entries[0]}")
            
            articles = []
            
            for entry in feed.entries[:ARTICLES_PER_SOURCE]:  # Limit to configured number of articles per feed
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
            
            full_text_result = fetch_full_text(article['link'])
            
            # Handle dict return value
            if isinstance(full_text_result, dict):
                article['full_text'] = full_text_result.get('plain', 'Content not available')
                
                # If date was extracted from the article page (ASIC), use it
                extracted_date = full_text_result.get('date')
                if extracted_date and not article.get('published'):
                    article['published'] = extracted_date
                    print(f"    Extracted date from article page: {extracted_date}")
            else:
                # Backward compatibility if it returns a string
                article['full_text'] = full_text_result
            
            # If date is still missing, try to extract from full text (use plain text version)
            if not article['published']:
                date_from_text = extract_date_from_text(article['full_text'])
                if date_from_text:
                    article['published'] = date_from_text
                    print(f"    Extracted date from text: {date_from_text}")
            
            # Initialize AI fields as empty - will be filled later only for new articles
            article['ai_summary'] = None
            article['risk_rating'] = None
            article['risk_rationale'] = None
            article['risk_confidence'] = None
            article['industries'] = None
            article['industry'] = None
            article['industry_rationale'] = None
            article['industry_confidence'] = None
            
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
        except ValueError:
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
    
    html_content = '''<!DOCTYPE html>
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
            overflow-x: hidden;
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
            max-width: 1800px;
            margin: 0 auto;
            padding: 0 1rem;
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
            max-width: 1800px;
            margin: 2rem auto;
            padding: 0 1rem 3rem;
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
            overflow: visible;
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
            margin-bottom: 0.75rem;
        }

        .article-title:hover {
            color: var(--primary);
        }

        .article-details-cell {
            padding: 1.25rem !important;
        }

        .article-detail-row {
            display: flex;
            align-items: center;
            padding: 0.5rem 0;
            border-bottom: 1px solid rgba(51, 65, 85, 0.3);
        }

        .article-detail-row:last-of-type {
            border-bottom: none;
        }

        .detail-label {
            font-weight: 600;
            color: var(--text-secondary);
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            min-width: 100px;
        }

        .detail-value {
            flex: 1;
        }

        .show-content-btn {
            width: 100%;
            padding: 0.625rem 1rem;
            background: var(--primary);
            color: white;
            border: none;
            border-radius: 0.5rem;
            font-size: 0.875rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            margin-top: 0.5rem;
        }

        .show-content-btn:hover {
            background: var(--primary-dark);
            transform: translateY(-1px);
            box-shadow: var(--shadow);
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

        .summary-cell {
            vertical-align: top;
            padding: 1.25rem !important;
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
            margin-right: 0.5rem;
            margin-bottom: 0.25rem;
        }

        .risk-badge.risk-minimal {
            background: #064e3b;
            color: #6ee7b7;
        }

        .risk-badge.risk-low {
            background: #14532d;
            color: #86efac;
        }

        .risk-badge.risk-moderate {
            background: #713f12;
            color: #fde047;
        }

        .risk-badge.risk-high {
            background: #7c2d12;
            color: #fca5a5;
        }

        .risk-badge.risk-critical {
            background: #7f1d1d;
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
            left: 100%;
            top: 50%;
            transform: translateY(-50%);
            margin-left: 1rem;
            background: var(--bg-primary);
            color: var(--text-primary);
            padding: 0.75rem 1rem;
            border-radius: 0.5rem;
            font-size: 0.75rem;
            font-weight: 400;
            line-height: 1.5;
            white-space: normal;
            width: 300px;
            max-width: 80vw;
            box-shadow: var(--shadow-lg);
            border: 1px solid var(--border);
            z-index: 1000;
            pointer-events: none;
            transition: opacity 0.3s, visibility 0.3s;
            text-transform: none;
            letter-spacing: normal;
        }

        .tooltip::before {
            content: '';
            position: absolute;
            right: 100%;
            top: 50%;
            transform: translateY(-50%);
            border: 6px solid transparent;
            border-right-color: var(--bg-primary);
        }

        .tooltip-container:hover .tooltip {
            visibility: visible;
            opacity: 1;
        }

        .info-buttons {
            display: flex;
            gap: 0.5rem;
        }

        .info-btn {
            padding: 0.5rem 1rem;
            background: var(--bg-secondary);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            font-size: 0.875rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            font-family: inherit;
        }

        .info-btn:hover {
            background: var(--bg-tertiary);
            color: var(--primary);
            border-color: var(--primary);
        }

        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            z-index: 9998;
            backdrop-filter: blur(4px);
        }

        .modal-overlay.active {
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .modal {
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 1rem;
            max-width: 900px;
            max-height: 85vh;
            width: 90%;
            overflow: hidden;
            box-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.3), 0 8px 10px -6px rgb(0 0 0 / 0.3);
            display: flex;
            flex-direction: column;
            z-index: 9999;
        }

        .modal-header {
            padding: 1.5rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: linear-gradient(to bottom, #334155, #1e293b);
        }

        .modal-title {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
            margin: 0;
        }

        .modal-close {
            background: transparent;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0.25rem 0.5rem;
            line-height: 1;
            transition: all 0.2s;
        }

        .modal-close:hover {
            color: var(--primary);
            transform: scale(1.1);
        }

        .modal-content {
            padding: 1.5rem;
            overflow-y: auto;
            flex: 1;
        }

        .modal-content::-webkit-scrollbar {
            width: 8px;
        }

        .modal-content::-webkit-scrollbar-track {
            background: var(--bg-secondary);
            border-radius: 4px;
        }

        .modal-content::-webkit-scrollbar-thumb {
            background: var(--secondary);
            border-radius: 4px;
        }

        .modal-content::-webkit-scrollbar-thumb:hover {
            background: var(--text-secondary);
        }

        .info-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
        }

        .info-table th,
        .info-table td {
            padding: 0.75rem 1rem;
            text-align: left;
            border: 1px solid var(--border);
        }

        .info-table th {
            background: var(--bg-secondary);
            font-weight: 600;
            color: var(--text-primary);
            font-size: 0.875rem;
            position: static;
        }

        .info-table td {
            color: var(--text-secondary);
            font-size: 0.875rem;
            line-height: 1.6;
        }

        .info-table tr:hover {
            background: var(--bg-secondary);
        }

        .risk-matrix-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
            table-layout: fixed;
        }

        .risk-matrix-table th,
        .risk-matrix-table td {
            padding: 1rem;
            text-align: center;
            border: 1px solid var(--border);
            font-size: 0.8125rem;
        }

        .risk-matrix-table th {
            background: var(--bg-secondary);
            font-weight: 600;
            color: var(--text-primary);
            position: static;
        }

        .risk-matrix-table .header-col {
            background: var(--bg-secondary);
            font-weight: 600;
            color: var(--text-primary);
            text-align: left;
        }

        .risk-cell-low {
            background: rgba(20, 83, 45, 0.3);
            color: #86efac;
            font-weight: 600;
        }

        .risk-cell-medium {
            background: rgba(113, 63, 18, 0.3);
            color: #fde047;
            font-weight: 600;
        }

        .risk-cell-high {
            background: rgba(124, 45, 18, 0.3);
            color: #fca5a5;
            font-weight: 600;
        }

        .risk-cell-extreme {
            background: rgba(124, 45, 18, 0.5);
            color: #ff6b6b;
            font-weight: 700;
        }

        .legend-section {
            margin-top: 1.5rem;
            padding: 1rem;
            background: var(--bg-secondary);
            border-radius: 0.5rem;
        }

        .legend-title {
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 0.75rem;
            font-size: 0.9375rem;
        }

        .legend-items {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 0.75rem;
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.8125rem;
            color: var(--text-secondary);
        }

        .legend-color {
            width: 2rem;
            height: 1.5rem;
            border-radius: 0.25rem;
            border: 1px solid var(--border);
        }

        .header-section-container {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 2rem;
        }

        .header-section {
            flex: 1;
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

        .sort-section {
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

        .pagination-container {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 0.5rem;
            padding: 1.5rem;
            background: var(--bg-primary);
            border-top: 1px solid var(--border);
        }

        .pagination-btn {
            padding: 0.5rem 1rem;
            background: var(--bg-secondary);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            font-size: 0.875rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            font-family: inherit;
        }

        .pagination-btn:hover:not(:disabled) {
            background: var(--bg-tertiary);
            color: var(--primary);
            border-color: var(--primary);
        }

        .pagination-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        .pagination-btn.active {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }

        .pagination-info {
            padding: 0.5rem 1rem;
            color: var(--text-secondary);
            font-size: 0.875rem;
            font-weight: 500;
        }

        @media (max-width: 768px) {
            .container {
                padding: 0 0.5rem 2rem;
                margin: 1rem auto;
            }

            .nav-container {
                padding: 0 0.5rem;
                flex-wrap: wrap;
            }

            .nav-right {
                flex-wrap: wrap;
            }

            .page-title {
                font-size: 1.5rem;
            }

            .filter-section, .sort-section {
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

            .article-detail-row {
                flex-direction: column;
                align-items: flex-start;
            }

            .detail-label {
                margin-bottom: 0.25rem;
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
        <div class="header-section-container">
            <div class="header-section">
                <h1 class="page-title">Latest Regulatory Updates</h1>
            </div>
            <div class="info-buttons">
                <button class="info-btn" onclick="showModal('industryModal')">Industry Definitions</button>
                <button class="info-btn" onclick="showModal('riskModal')">Impact Scale</button>
            </div>
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

        <div class="sort-section">
            <span class="filter-label">Sort by:</span>
            <div class="filter-buttons">
                <button class="filter-btn" data-sort-column="date" onclick="toggleSort('date')">Date ↓</button>
                <button class="filter-btn" data-sort-column="title" onclick="toggleSort('title')">Title ↑</button>
                <button class="filter-btn" data-sort-column="industry" onclick="toggleSort('industry')">Industry ↑</button>
                <button class="filter-btn" data-sort-column="risk" onclick="toggleSort('risk')">Impact ↓</button>
            </div>
        </div>

        <div class="content-card">
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 40%;">Article Details</th>
                            <th style="width: 60%;">Summary</th>
                        </tr>
                    </thead>
                    <tbody id="articleTableBody">
'''
    
    for article in articles:
        source_class = article['source'].lower().split()[0]
        
        # Get rationales and ratings
        risk_rating = article.get('risk_rating', 'Not Rated')
        risk_rationale = article.get('risk_rationale', 'Risk assessment unavailable')
        risk_confidence = article.get('risk_confidence', 'N/A')
        
        # Get industries (now supports multiple)
        industries = article.get('industries', None)
        if industries is None or not isinstance(industries, list) or len(industries) == 0:
            industries = [article.get('industry', 'Other')]  # Fallback to old format
        
        industry_rationale = article.get('industry_rationale', 'Industry classification unavailable')
        industry_confidence = article.get('industry_confidence', 'N/A')
        ai_summary = article.get('ai_summary', 'Summary not available')
        
        # Escape HTML for rationales (for tooltip attributes)
        risk_rationale = html.escape(risk_rationale)
        risk_confidence = html.escape(str(risk_confidence))
        industry_rationale = html.escape(industry_rationale)
        industry_confidence = html.escape(str(industry_confidence))
        # Also escape summary for display in content
        ai_summary = html.escape(ai_summary)
        
        # Get plain text version for display
        full_text_display = article.get('full_text', '')
        
        if not full_text_display:
            full_text_display = 'Content not extracted - article may be new or extraction failed'
        elif full_text_display in ['Content not available', 'Insufficient content extracted']:
            full_text_display = f'{full_text_display} - The page structure may not be supported. View original article: {article["link"]}'
        elif full_text_display.startswith('Error'):
            full_text_display = f'{full_text_display} - View original article: {article["link"]}'
        
        # Remove any newlines/line breaks (ensure continuous text)
        if '\n' in full_text_display:
            full_text_display = ' '.join(full_text_display.split())
        
        # Escape HTML special characters to display as plain text
        full_text_display = html.escape(full_text_display)
        
        # Generate unique ID for this article's collapsible section
        article_id = f"article-{abs(hash(article['link']))}"
        
        # Determine risk color class based on new impact ratings
        risk_class = 'risk-low'
        risk_lower = risk_rating.lower()
        if risk_lower == 'minimal':
            risk_class = 'risk-minimal'
        elif risk_lower == 'low':
            risk_class = 'risk-low'
        elif risk_lower == 'moderate':
            risk_class = 'risk-moderate'
        elif risk_lower == 'high':
            risk_class = 'risk-high'
        elif risk_lower == 'critical':
            risk_class = 'risk-critical'
        
        # Create industry badges HTML (support for multiple industries)
        industry_badges_html = ''
        for ind in industries:
            ind_escaped = html.escape(ind)
            # Create tooltip with both rationale and confidence
            tooltip_text = f"<strong>Confidence:</strong> {industry_confidence}<br><br>{industry_rationale}"
            industry_badges_html += f'''<span class="industry-badge tooltip-container">
                                            {ind_escaped}
                                            <span class="tooltip">{tooltip_text}</span>
                                        </span> '''
        
        # For filtering, use first industry (or join all for multi-industry filtering)
        first_industry = industries[0] if industries else 'Other'
        
        html_content += f'''
                        <tr class="article-row" data-source="{article['source']}" data-title="{article['title'].lower()}" data-date="{article['published']}" data-industry="{first_industry.lower()}" data-risk="{risk_rating.lower()}">
                            <td class="article-details-cell">
                                <div class="article-detail-row">
                                    <span class="detail-label">Source</span>
                                    <div class="detail-value">
                                        <span class="source-tag {source_class}">{article['source']}</span>
                                    </div>
                                </div>
                                <div class="article-detail-row">
                                    <span class="detail-label">Title</span>
                                    <div class="detail-value">
                                        <a href="{article['link']}" target="_blank" class="article-title">{article['title']}</a>
                                    </div>
                                </div>
                                <div class="article-detail-row">
                                    <span class="detail-label">Date</span>
                                    <div class="detail-value">
                                        <span class="article-date">{format_date(article['published'])}</span>
                                    </div>
                                </div>
                                <div class="article-detail-row">
                                    <span class="detail-label">Industry</span>
                                    <div class="detail-value">
                                        {industry_badges_html}
                                    </div>
                                </div>
                                <div class="article-detail-row">
                                    <span class="detail-label">Impact</span>
                                    <div class="detail-value">
                                        <span class="risk-badge {risk_class} tooltip-container">
                                            {risk_rating}
                                            <span class="tooltip"><strong>Confidence:</strong> {risk_confidence}<br><br>{risk_rationale}</span>
                                        </span>
                                    </div>
                                </div>
                                <button class="show-content-btn" onclick="toggleFullText('{article_id}')" id="{article_id}-btn">
                                    Show Full Content
                                </button>
                            </td>
                            <td class="summary-cell">
                                <div class="summary-text">{ai_summary}</div>
                            </td>
                        </tr>
                        <tr class="full-text-row" id="{article_id}" style="display: none;">
                            <td colspan="2">
                                <div class="article-content">{full_text_display}</div>
                            </td>
                        </tr>
'''
    
    html_content += '''
                    </tbody>
                </table>
            </div>
            <div class="pagination-container">
                <button class="pagination-btn" onclick="changePage(-1)" id="prevBtn">Previous</button>
                <span class="pagination-info" id="pageInfo">Page 1</span>
                <button class="pagination-btn" onclick="changePage(1)" id="nextBtn">Next</button>
            </div>
        </div>

        <div class="footer">
            <p>Displaying all collected articles from ACCC, ASIC, APRA, AUSTRAC, and RBA</p>
            <div class="footer-divider"></div>
            <p>Total articles collected: ''' + str(len(articles)) + ''' | Data collection started: ''' + DATA_COLLECTION_START_DATE + '''</p>
            <p>Automatically updated every 12 hours</p>
        </div>
    </div>

    <!-- Industry Definitions Modal -->
    <div class="modal-overlay" id="industryModal" onclick="closeModalOnBackdrop(event, 'industryModal')">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2 class="modal-title">Industry Definitions (GICS Industry Groups)</h2>
                <button class="modal-close" onclick="closeModal('industryModal')">&times;</button>
            </div>
            <div class="modal-content">
                <p style="color: var(--text-secondary); margin-bottom: 1.5rem; line-height: 1.6;">
                    Articles are classified using the Global Industry Classification Standard (GICS) at the Industry Group level. Multiple industries may be assigned when a regulatory release impacts more than one sector.
                </p>
                <table class="info-table">
                    <thead>
                        <tr>
                            <th style="width: 30%;">Industry Group</th>
                            <th style="width: 70%;">Definition</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>Energy</strong></td>
                            <td>Companies involved in the exploration, production, refining, and distribution of oil, gas, coal, and related fuels, as well as energy equipment and services.</td>
                        </tr>
                        <tr>
                            <td><strong>Materials</strong></td>
                            <td>Companies engaged in the extraction or processing of raw materials such as metals, chemicals, construction materials, paper, and forestry products.</td>
                        </tr>
                        <tr>
                            <td><strong>Capital Goods</strong></td>
                            <td>Producers of machinery, construction equipment, aerospace and defense products, and other capital equipment used in manufacturing and infrastructure.</td>
                        </tr>
                        <tr>
                            <td><strong>Commercial & Professional Services</strong></td>
                            <td>Providers of business support, outsourcing, employment, and professional consulting services.</td>
                        </tr>
                        <tr>
                            <td><strong>Transportation</strong></td>
                            <td>Companies providing air, marine, road, and rail transportation services, logistics, and related infrastructure.</td>
                        </tr>
                        <tr>
                            <td><strong>Automobiles & Components</strong></td>
                            <td>Manufacturers and suppliers of automobiles, motorcycles, auto parts, and related equipment.</td>
                        </tr>
                        <tr>
                            <td><strong>Consumer Durables & Apparel</strong></td>
                            <td>Producers of household durable goods such as furniture, home appliances, and leisure products, as well as clothing, footwear, and textiles.</td>
                        </tr>
                        <tr>
                            <td><strong>Consumer Services</strong></td>
                            <td>Providers of consumer-focused services including hotels, restaurants, leisure facilities, and education services.</td>
                        </tr>
                        <tr>
                            <td><strong>Media & Entertainment</strong></td>
                            <td>Companies engaged in broadcasting, publishing, advertising, digital content creation, gaming, and entertainment production.</td>
                        </tr>
                        <tr>
                            <td><strong>Retailing</strong></td>
                            <td>Businesses engaged in selling consumer goods through traditional or online retail channels, excluding food and staples retailing.</td>
                        </tr>
                        <tr>
                            <td><strong>Food & Staples Retailing</strong></td>
                            <td>Retailers of food, drug, and other staple goods including supermarkets and convenience stores.</td>
                        </tr>
                        <tr>
                            <td><strong>Food, Beverage & Tobacco</strong></td>
                            <td>Producers of packaged foods, beverages (alcoholic and non-alcoholic), and tobacco products.</td>
                        </tr>
                        <tr>
                            <td><strong>Household & Personal Products</strong></td>
                            <td>Manufacturers of cleaning products, personal care items, cosmetics, and other household consumables.</td>
                        </tr>
                        <tr>
                            <td><strong>Health Care Equipment & Services</strong></td>
                            <td>Providers of healthcare equipment, medical devices, healthcare facilities, and related services.</td>
                        </tr>
                        <tr>
                            <td><strong>Pharmaceuticals, Biotechnology & Life Sciences</strong></td>
                            <td>Companies engaged in the research, development, and production of pharmaceuticals, biotech therapies, and life science tools.</td>
                        </tr>
                        <tr>
                            <td><strong>Banks</strong></td>
                            <td>Institutions providing commercial, retail, and investment banking services.</td>
                        </tr>
                        <tr>
                            <td><strong>Diversified Financials</strong></td>
                            <td>Providers of investment services, asset management, brokerage, consumer finance, and other non-bank financial activities.</td>
                        </tr>
                        <tr>
                            <td><strong>Insurance</strong></td>
                            <td>Companies offering life, health, property, casualty, or reinsurance products.</td>
                        </tr>
                        <tr>
                            <td><strong>Real Estate</strong></td>
                            <td>Developers, owners, and operators of residential, commercial, or industrial real estate, as well as REITs.</td>
                        </tr>
                        <tr>
                            <td><strong>Software & Services</strong></td>
                            <td>Developers and providers of software, IT consulting, and data processing services.</td>
                        </tr>
                        <tr>
                            <td><strong>Technology Hardware & Equipment</strong></td>
                            <td>Manufacturers of computers, communications equipment, and related technology hardware.</td>
                        </tr>
                        <tr>
                            <td><strong>Semiconductors & Semiconductor Equipment</strong></td>
                            <td>Producers of semiconductors, microchips, and the equipment used to manufacture them.</td>
                        </tr>
                        <tr>
                            <td><strong>Telecommunication Services</strong></td>
                            <td>Providers of fixed-line, wireless, broadband, and internet communication services.</td>
                        </tr>
                        <tr>
                            <td><strong>Utilities</strong></td>
                            <td>Providers of electricity, gas, water, and renewable energy generation and distribution services.</td>
                        </tr>
                        <tr>
                            <td><strong>All</strong></td>
                            <td>For announcements or regulations that broadly and directly affect all industries without sector distinction.</td>
                        </tr>
                        <tr>
                            <td><strong>Other</strong></td>
                            <td>For announcements that are cross-sectoral, governmental, macroeconomic, or otherwise not specific to one or more industries above.</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Impact Scale Modal -->
    <div class="modal-overlay" id="riskModal" onclick="closeModalOnBackdrop(event, 'riskModal')">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2 class="modal-title">Regulatory Impact Scale (1-5)</h2>
                <button class="modal-close" onclick="closeModal('riskModal')">&times;</button>
            </div>
            <div class="modal-content">
                <p style="color: var(--text-secondary); margin-bottom: 1.5rem; line-height: 1.6;">
                    Each regulatory media release is assessed on a 1-5 scale based on its potential impact on organizations, industries, and markets. The assessment considers scope, depth, timing, compliance risk, and strategic significance.
                </p>
                
                <div class="legend-section" style="margin-top: 0;">
                    <div class="legend-title">Impact Rating Scale</div>
                    <table class="info-table">
                        <thead>
                            <tr>
                                <th style="width: 25%;">Rating</th>
                                <th style="width: 75%;">Description</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td><strong>1 - Minimal Impact</strong></td>
                                <td>Negligible operational or financial effect. No new compliance requirements. The media release provides information or minor clarifications only.</td>
                            </tr>
                            <tr>
                                <td><strong>2 - Low Impact</strong></td>
                                <td>Minor procedural or reporting updates with limited cost or effort. Minimal risk of non-compliance. Impact confined to specific areas of operation.</td>
                            </tr>
                            <tr>
                                <td><strong>3 - Moderate Impact</strong></td>
                                <td>Noticeable process or system changes required. May necessitate moderate resources, training, or updates. Compliance risk is manageable.</td>
                            </tr>
                            <tr>
                                <td><strong>4 - High Impact</strong></td>
                                <td>Significant operational or financial implications. Requires coordinated implementation, new systems, or material policy changes. Elevated compliance or strategic risk.</td>
                            </tr>
                            <tr>
                                <td><strong>5 - Critical Impact</strong></td>
                                <td>Major change to business models, core processes, or regulatory obligations. High cost of compliance or severe risk of non-compliance. Board-level attention required.</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <div class="legend-section">
                    <div class="legend-title">Evaluation Dimensions</div>
                    <table class="info-table">
                        <tbody>
                            <tr>
                                <td style="width: 30%;"><strong>Scope</strong></td>
                                <td>Is the regulation industry-wide or niche? Does it affect many or few organizations?</td>
                            </tr>
                            <tr>
                                <td><strong>Depth</strong></td>
                                <td>What degree of change to policy, operations, or reporting is required?</td>
                            </tr>
                            <tr>
                                <td><strong>Timing</strong></td>
                                <td>How immediate is the required action? Is there sufficient lead time?</td>
                            </tr>
                            <tr>
                                <td><strong>Compliance Risk</strong></td>
                                <td>What are the legal, financial, or reputational consequences of non-compliance?</td>
                            </tr>
                            <tr>
                                <td><strong>Strategic Significance</strong></td>
                                <td>Does this change the competitive or operational landscape?</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <div class="legend-section">
                    <div class="legend-title">Confidence Rating</div>
                    <p style="color: var(--text-secondary); margin-bottom: 1rem; font-size: 0.875rem; line-height: 1.6;">
                        Each assessment includes a confidence rating (High/Medium/Low) that reflects the clarity and completeness of the information in the media release:
                    </p>
                    <div class="legend-items" style="display: flex; flex-direction: column; gap: 0.75rem;">
                        <div style="color: var(--text-secondary); font-size: 0.8125rem;">
                            <strong style="color: var(--text-primary);">High:</strong> The release is clear, specific, and detailed; assessment is well-supported with unambiguous information
                        </div>
                        <div style="color: var(--text-secondary); font-size: 0.8125rem;">
                            <strong style="color: var(--text-primary);">Medium:</strong> Adequate detail but some ambiguity or unknowns remain; assessment is reasonably confident
                        </div>
                        <div style="color: var(--text-secondary); font-size: 0.8125rem;">
                            <strong style="color: var(--text-primary);">Low:</strong> Limited details provided; assessment based on partial, inferred, or vague information; impact cannot be assessed reliably
                        </div>
                    </div>
                </div>

                <div class="legend-section">
                    <div class="legend-title">Color Legend</div>
                    <div class="legend-items">
                        <div class="legend-item">
                            <div class="legend-color" style="background: #064e3b;"></div>
                            <span><strong>Minimal Impact:</strong> Informational only</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color" style="background: #14532d;"></div>
                            <span><strong>Low Impact:</strong> Minor updates required</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color" style="background: #713f12;"></div>
                            <span><strong>Moderate Impact:</strong> Noticeable changes needed</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color" style="background: #7c2d12;"></div>
                            <span><strong>High Impact:</strong> Significant implications</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color" style="background: #7f1d1d;"></div>
                            <span><strong>Critical Impact:</strong> Major organizational change</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function showModal(modalId) {
            document.getElementById(modalId).classList.add('active');
            document.body.style.overflow = 'hidden'; // Prevent background scrolling
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('active');
            document.body.style.overflow = ''; // Restore scrolling
        }

        function closeModalOnBackdrop(event, modalId) {
            if (event.target.classList.contains('modal-overlay')) {
                closeModal(modalId);
            }
        }

        // Close modal on Escape key
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeModal('industryModal');
                closeModal('riskModal');
            }
        });

        function toggleFullText(articleId) {
            const fullTextRow = document.getElementById(articleId);
            const button = document.getElementById(articleId + '-btn');
            
            if (fullTextRow.style.display === 'none') {
                fullTextRow.style.display = 'table-row';
                button.textContent = 'Hide Full Content';
            } else {
                fullTextRow.style.display = 'none';
                button.textContent = 'Show Full Content';
            }
        }

        function getArticleId(row) {
            // Extract article ID from the button's onclick attribute
            const button = row.querySelector('.show-content-btn');
            if (button) {
                const onclickAttr = button.getAttribute('onclick');
                const match = onclickAttr.match(/toggleFullText\('(.+?)'\)/);
                return match ? match[1] : null;
            }
            return null;
        }

        function sortArticles(column, ascending) {
            const tbody = document.getElementById('articleTableBody');
            const rows = Array.from(tbody.querySelectorAll('.article-row'));
            
            // Store references to full-text rows BEFORE clearing tbody
            const fullTextRows = {};
            rows.forEach(row => {
                const articleId = getArticleId(row);
                fullTextRows[articleId] = document.getElementById(articleId);
            });
            
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
                    // Impact rating sorting: Critical > High > Moderate > Low > Minimal > Not Rated
                    const riskOrder = { 'critical': 5, 'high': 4, 'moderate': 3, 'low': 2, 'minimal': 1, 'not rated': 0, 'assessment failed': -1 };
                    aValue = riskOrder[a.dataset.risk] || 0;
                    bValue = riskOrder[b.dataset.risk] || 0;
                }
                
                if (aValue < bValue) return ascending ? -1 : 1;
                if (aValue > bValue) return ascending ? 1 : -1;
                return 0;
            });
            
            // Clear tbody
            tbody.innerHTML = '';
            
            // Re-append rows with their corresponding full-text rows in correct order
            rows.forEach(row => {
                const articleId = getArticleId(row);
                const fullTextRow = fullTextRows[articleId]; // Use stored reference
                
                // Append article row
                tbody.appendChild(row);
                
                // Append corresponding full-text row immediately after
                if (fullTextRow) {
                    tbody.appendChild(fullTextRow);
                }
            });
            
            // Reset to page 1 after sorting
            currentPage = 1;
            updatePagination();
        }

        // Track sort state for each column
        const sortState = {
            date: false,      // false = descending (newest first)
            title: true,      // true = ascending (A-Z)
            industry: true,   // true = ascending (A-Z)
            risk: false       // false = descending (highest risk first)
        };

        function toggleSort(column) {
            // Toggle the sort direction for this column
            sortState[column] = !sortState[column];
            
            // Update button text and active state
            const buttons = document.querySelectorAll('.sort-section .filter-btn');
            buttons.forEach(btn => {
                const btnColumn = btn.dataset.sortColumn;
                if (btnColumn === column) {
                    btn.classList.add('active');
                    // Update arrow based on column type and sort direction
                    const arrow = sortState[column] ? '↑' : '↓';
                    const columnName = column.charAt(0).toUpperCase() + column.slice(1);
                    btn.textContent = `${columnName} ${arrow}`;
                } else {
                    btn.classList.remove('active');
                }
            });
            
            // Perform the sort
            sortArticles(column, sortState[column]);
        }

        function filterArticles(source) {
            const rows = document.querySelectorAll('.article-row');
            const buttons = document.querySelectorAll('.filter-section .filter-btn');
            
            buttons.forEach(btn => {
                if (btn.dataset.filter === source) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            
            rows.forEach((row) => {
                const rowSource = row.dataset.source;
                const articleId = getArticleId(row);
                const fullTextRow = document.getElementById(articleId);
                const button = document.getElementById(articleId + '-btn');
                
                if (source === 'all' || rowSource === source) {
                    // Show the article row
                    row.classList.remove('hidden');
                    row.style.display = '';
                    
                    // Keep full-text row hidden initially, but make it available
                    if (fullTextRow) {
                        fullTextRow.classList.remove('hidden');
                        // Don't change the display state - maintain whether it was open or closed
                    }
                } else {
                    // Hide the article row
                    row.classList.add('hidden');
                    row.style.display = 'none';
                    
                    // Hide the full-text row
                    if (fullTextRow) {
                        fullTextRow.classList.add('hidden');
                        fullTextRow.style.display = 'none';
                        
                        // Reset button text when hiding
                        if (button) {
                            button.textContent = 'Show Full Content';
                        }
                    }
                }
            });
            
            // Reset to page 1 after filtering
            currentPage = 1;
            updatePagination();
        }

        // Pagination variables
        let currentPage = 1;
        const articlesPerPage = 10;

        function getVisibleArticleRows() {
            // Get all article rows that are not filtered out (not hidden by filter)
            return Array.from(document.querySelectorAll('.article-row')).filter(row => {
                return !row.classList.contains('hidden');
            });
        }

        function updatePagination() {
            const visibleRows = getVisibleArticleRows();
            const totalPages = Math.ceil(visibleRows.length / articlesPerPage);
            
            // Update page info
            document.getElementById('pageInfo').textContent = `Page ${currentPage} of ${totalPages}`;
            
            // Update button states
            document.getElementById('prevBtn').disabled = currentPage === 1;
            document.getElementById('nextBtn').disabled = currentPage >= totalPages;
            
            // Show/hide rows based on current page
            visibleRows.forEach((row, index) => {
                const articleId = getArticleId(row);
                const fullTextRow = document.getElementById(articleId);
                const button = document.getElementById(articleId + '-btn');
                
                const startIndex = (currentPage - 1) * articlesPerPage;
                const endIndex = startIndex + articlesPerPage;
                
                if (index >= startIndex && index < endIndex) {
                    // Show this row (it's on the current page)
                    row.style.display = '';
                    // Keep full-text row state as is (if it was open, keep it open)
                } else {
                    // Hide this row (it's not on the current page)
                    row.style.display = 'none';
                    
                    // Also hide and reset its full-text row
                    if (fullTextRow) {
                        fullTextRow.style.display = 'none';
                        if (button) {
                            button.textContent = 'Show Full Content';
                        }
                    }
                }
            });
        }

        function changePage(direction) {
            const visibleRows = getVisibleArticleRows();
            const totalPages = Math.ceil(visibleRows.length / articlesPerPage);
            
            currentPage += direction;
            
            // Clamp to valid range
            if (currentPage < 1) currentPage = 1;
            if (currentPage > totalPages) currentPage = totalPages;
            
            updatePagination();
            
            // Scroll to top of page
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        // Initialize pagination on page load
        document.addEventListener('DOMContentLoaded', function() {
            updatePagination();
            
            // Set Date button as active by default (matches server-side sort)
            const dateBtn = document.querySelector('[data-sort-column="date"]');
            if (dateBtn) {
                dateBtn.classList.add('active');
            }
        });
    </script>
</body>
</html>
'''
    
    return html_content

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
        
        # Industry fields (now with multiple industries support)
        industries = article.get('industries', [article.get('industry', 'Other')])
        if isinstance(industries, list):
            ET.SubElement(entry, 'industries').text = ', '.join(industries)
        else:
            ET.SubElement(entry, 'industries').text = str(industries)
        ET.SubElement(entry, 'industry').text = article.get('industry', 'Other')  # First industry for compatibility
        ET.SubElement(entry, 'industry_rationale').text = article.get('industry_rationale', '')
        ET.SubElement(entry, 'industry_confidence').text = str(article.get('industry_confidence', 'N/A'))
        
        # Impact/Risk fields
        ET.SubElement(entry, 'risk_rating').text = article.get('risk_rating', 'Not Rated')
        ET.SubElement(entry, 'risk_rationale').text = article.get('risk_rationale', '')
        ET.SubElement(entry, 'risk_confidence').text = str(article.get('risk_confidence', 'N/A'))
        
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
    
    # Clean up HTML from existing database entries
    print("\nCleaning up HTML formatting from existing articles...")
    existing_articles = cleanup_html_from_database(existing_articles)
    
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
    print(f"[OK] HTML saved to {OUTPUT_HTML}")
    
    # Generate XML
    print("Generating XML...")
    xml = generate_xml(all_articles)
    with open(OUTPUT_XML, 'w', encoding='utf-8') as f:
        f.write(xml)
    print(f"[OK] XML saved to {OUTPUT_XML}")
    
    print("-" * 50)
    print("[DONE] All files generated successfully!")
    print(f"Database contains {len(all_articles)} total articles")

if __name__ == "__main__":
    main()
