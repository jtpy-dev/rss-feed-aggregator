import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom
import time

# Configuration
FEED_SOURCES = [
    {
        'url': 'https://www.accc.gov.au/rss/news_centre.xml',
        'name': 'ACCC News',
        'type': 'rss'
    },
    {
        'url': 'https://www.austrac.gov.au/news-and-media/media-release',
        'name': 'AUSTRAC Media Releases',
        'type': 'webpage'
    },
    {
        'url': 'https://www.apra.gov.au/news-and-publications',
        'name': 'APRA News',
        'type': 'webpage'
    }
]

MAX_ARTICLES = 20
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
        response = requests.get(url, headers=headers, timeout=10)
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

def fetch_austrac_news():
    """Scrape AUSTRAC media release page to create RSS-like entries"""
    try:
        url = 'https://www.austrac.gov.au/news-and-media/media-release'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        articles = []
        
        # Find news items - AUSTRAC uses view-content structure
        news_items = soup.select('.view-content .views-row, .view-media-release .views-row, .views-row')[:10]
        
        for item in news_items:
            try:
                title_elem = item.select_one('h3 a, h2 a, .title a, .views-field-title a')
                link_elem = item.select_one('a')
                
                # Try multiple date selectors - dates are often ABOVE the title
                date_elem = item.select_one(
                    '.date, time, '
                    '.views-field-created, .field--name-created, '
                    '.views-field-field-date, .field--name-field-date, '
                    '.views-field-field-media-release-date, '
                    'span.date, div.date, p.date, '
                    '.field--type-datetime, '
                    '[class*="date"]'
                )
                
                # If no date element found, try to extract from text content
                date_text = ''
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                    print(f"    Found date element: {date_text}")
                else:
                    # Look for date in the entire item's text (including above title)
                    item_text = item.get_text()
                    date_text = extract_date_from_text(item_text)
                    if date_text:
                        print(f"    Extracted date from item text: {date_text}")
                
                summary_elem = item.select_one('.summary, .views-field-body, .field--name-body, p')
                
                if title_elem and link_elem:
                    article_url = link_elem.get('href', '')
                    if article_url.startswith('/'):
                        article_url = 'https://www.austrac.gov.au' + article_url
                    
                    article = {
                        'title': title_elem.get_text(strip=True),
                        'link': article_url,
                        'published': date_text or '',
                        'summary': summary_elem.get_text(strip=True)[:300] if summary_elem else '',
                    }
                    articles.append(article)
            except Exception as e:
                print(f"Error parsing AUSTRAC article: {e}")
                continue
        
        return articles
    except Exception as e:
        print(f"Error fetching AUSTRAC news: {e}")
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
        
        # Find news items (adjust selectors based on actual page structure)
        news_items = soup.select('.view-content .views-row')[:10]
        
        for item in news_items:
            try:
                title_elem = item.select_one('h3 a, h2 a, .title a')
                link_elem = item.select_one('a')
                date_elem = item.select_one('.date, time, .field--name-created')
                summary_elem = item.select_one('.summary, .field--name-body, p')
                
                if title_elem and link_elem:
                    article_url = link_elem.get('href', '')
                    if article_url.startswith('/'):
                        article_url = 'https://www.apra.gov.au' + article_url
                    
                    article = {
                        'title': title_elem.get_text(strip=True),
                        'link': article_url,
                        'published': date_elem.get_text(strip=True) if date_elem else '',
                        'summary': summary_elem.get_text(strip=True)[:300] if summary_elem else '',
                    }
                    articles.append(article)
            except Exception as e:
                print(f"Error parsing APRA article: {e}")
                continue
        
        return articles
    except Exception as e:
        print(f"Error fetching APRA news: {e}")
        return []

def parse_rss_feed(url):
    """Parse RSS feed and extract entries"""
    try:
        feed = feedparser.parse(url)
        articles = []
        
        for entry in feed.entries[:15]:
            article = {
                'title': entry.get('title', 'No title'),
                'link': entry.get('link', ''),
                'published': entry.get('published', entry.get('updated', '')),
                'summary': entry.get('summary', entry.get('description', ''))
            }
            articles.append(article)
        
        return articles
    except Exception as e:
        print(f"Error parsing RSS feed {url}: {e}")
        return []

def process_feeds():
    """Process all feeds and extract full text"""
    all_articles = []
    
    for source in FEED_SOURCES:
        print(f"Processing {source['name']}...")
        
        if source['type'] == 'rss':
            articles = parse_rss_feed(source['url'])
        elif source['type'] == 'webpage':
            if 'austrac.gov.au' in source['url']:
                articles = fetch_austrac_news()
            elif 'apra.gov.au' in source['url']:
                articles = fetch_apra_news()
            else:
                continue
        else:
            continue
        
        # Add source name and fetch full text
        for article in articles:
            article['source'] = source['name']
            print(f"  Fetching full text for: {article['title'][:50]}...")
            article['full_text'] = fetch_full_text(article['link'])
            
            # If date is missing for AUSTRAC articles, try to extract from full text
            if not article['published'] and 'austrac.gov.au' in source['url']:
                date_from_text = extract_date_from_text(article['full_text'])
                if date_from_text:
                    article['published'] = date_from_text
                    print(f"    Extracted date from text: {date_from_text}")
            
            time.sleep(0.5)  # Be polite, don't hammer servers
        
        all_articles.extend(articles)
    
    # Sort by date (newest first) and limit to MAX_ARTICLES
    all_articles.sort(key=lambda x: parse_date(x['published']), reverse=True)
    return all_articles[:MAX_ARTICLES]

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
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Australian Financial Regulators News Feed</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #f5f7fa;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            padding: 30px;
        }}

        header {{
            border-bottom: 3px solid #007bff;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}

        h1 {{
            color: #1a1a1a;
            font-size: 28px;
            margin-bottom: 10px;
        }}

        .meta-info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            color: #666;
            font-size: 14px;
        }}

        .last-updated {{
            font-weight: 500;
        }}

        .actions {{
            display: flex;
            gap: 10px;
        }}

        .btn {{
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: all 0.3s;
        }}

        .btn-primary {{
            background: #007bff;
            color: white;
        }}

        .btn-primary:hover {{
            background: #0056b3;
        }}

        .btn-secondary {{
            background: #6c757d;
            color: white;
        }}

        .btn-secondary:hover {{
            background: #545b62;
        }}

        .table-container {{
            overflow-x: auto;
            margin-top: 20px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        th {{
            background: #343a40;
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            position: sticky;
            top: 0;
            z-index: 10;
        }}

        td {{
            padding: 15px;
            border-bottom: 1px solid #e9ecef;
            vertical-align: top;
        }}

        tr:hover {{
            background: #f8f9fa;
        }}

        .source-badge {{
            display: inline-block;
            padding: 4px 10px;
            background: #e7f3ff;
            color: #0056b3;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 8px;
        }}

        .article-title {{
            color: #007bff;
            font-weight: 600;
            font-size: 15px;
            text-decoration: none;
            display: block;
            margin-bottom: 5px;
        }}

        .article-title:hover {{
            text-decoration: underline;
        }}

        .article-date {{
            color: #666;
            font-size: 13px;
        }}

        .article-content {{
            color: #333;
            font-size: 14px;
            line-height: 1.6;
            max-height: 400px;
            overflow-y: auto;
            padding: 12px;
            background: #f8f9fa;
            border-radius: 4px;
            border-left: 3px solid #007bff;
        }}

        .article-content::-webkit-scrollbar {{
            width: 8px;
        }}

        .article-content::-webkit-scrollbar-track {{
            background: #f1f1f1;
            border-radius: 4px;
        }}

        .article-content::-webkit-scrollbar-thumb {{
            background: #888;
            border-radius: 4px;
        }}

        .article-content::-webkit-scrollbar-thumb:hover {{
            background: #555;
        }}

        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e9ecef;
            text-align: center;
            color: #666;
            font-size: 14px;
        }}

        @media (max-width: 768px) {{
            .container {{
                padding: 15px;
            }}

            table {{
                font-size: 13px;
            }}

            th, td {{
                padding: 10px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📰 Australian Financial Regulators News Feed</h1>
            <div class="meta-info">
                <div class="last-updated">
                    Last Updated: {datetime.now().strftime('%d %B %Y at %H:%M AEDT')}
                </div>
                <div class="actions">
                    <a href="feed-data.xml" class="btn btn-secondary" download>Download XML</a>
                    <a href="https://github.com/YOUR_USERNAME/YOUR_REPO/actions" class="btn btn-primary" target="_blank">Trigger Refresh</a>
                </div>
            </div>
        </header>

        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 25%;">Title</th>
                        <th style="width: 12%;">Date</th>
                        <th style="width: 63%;">Full Text</th>
                    </tr>
                </thead>
                <tbody>
'''
    
    for article in articles:
        html += f'''
                    <tr>
                        <td>
                            <span class="source-badge">{article['source']}</span>
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
    
    html += f'''
                </tbody>
            </table>
        </div>

        <div class="footer">
            <p>Showing latest {len(articles)} articles from ACCC, AUSTRAC, and APRA</p>
            <p>Updates automatically every 12 hours via GitHub Actions</p>
        </div>
    </div>
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
        ET.SubElement(entry, 'fullText').text = article['full_text']
    
    # Pretty print XML
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent='  ')
    return xml_str

def generate_json(articles):
    """Generate JSON file for alternative consumption"""
    data = {
        'updated': datetime.now().isoformat(),
        'count': len(articles),
        'articles': articles
    }
    return json.dumps(data, indent=2, ensure_ascii=False)

def main():
    print("Starting RSS feed processing...")
    print(f"Target: {MAX_ARTICLES} latest articles")
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
