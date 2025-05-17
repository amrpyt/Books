import asyncio
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright
import argparse
import os
from datetime import datetime

async def explore_site_structure(page):
    """Explore the website structure to better understand how to extract content."""
    print("📚 Analyzing website structure...")
    
    # Check for pagination element using valid selectors
    pagination_info = await page.evaluate("""() => {
        // Common pagination patterns
        const patterns = [
            'div.pagination',
            'div[class*="pagination"]',
            'div.pages',
            'div.flex-grow-1',
            'div[class*="page-count"]'
        ];
        
        for (const selector of patterns) {
            const element = document.querySelector(selector);
            if (element && element.textContent.includes('/')) {
                return element.textContent;
            }
        }
        
        // Fallback: look for any element containing page numbers
        const elements = document.querySelectorAll('div');
        for (const el of elements) {
            if (el.textContent.match(/\\d+\\s*\\/\\s*\\d+/)) {
                return el.textContent;
            }
        }
        
        return null;
    }""")
    
    # Detect content structure
    content_structure = await page.evaluate("""() => {
        // Try different content selectors
        const selectors = {
            articles: document.querySelectorAll('article'),
            bookContent: document.querySelectorAll('div.book-content, div.content'),
            pageContent: document.querySelectorAll('div.page-content'),
            mainContent: document.querySelectorAll('main, div.main-content'),
            textContent: document.querySelectorAll('div.text-content, div[class*="text"]')
        };
        
        // Find which selectors have content
        const results = {};
        for (const [key, elements] of Object.entries(selectors)) {
            results[key] = {
                count: elements.length,
                hasText: Array.from(elements).some(el => el.textContent.trim().length > 100)
            };
        }
        
        return results;
    }""")
    
    return {
        "pagination": pagination_info,
        "content_structure": content_structure
    }

async def extract_book(url, output_dir=None):
    """Extract all pages of a book from ketabonline.com"""
    try:
        # Create output directory if specified
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        # Launch browser with Arabic language support
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                locale='ar-SA'  # Set Arabic locale
            )
            page = await context.new_page()
            
            print(f"🔍 Accessing {url}...")
            await page.goto(url, timeout=60000)  # 60 second timeout
            await page.wait_for_load_state("networkidle")
            
            # Extract book name for file naming
            title = await page.title()
            book_name = title.split('|')[0].strip().replace(' ', '_')
            book_name = re.sub(r'[^\w\u0600-\u06FF]+', '_', book_name)  # Keep Arabic and word chars
            
            # Explore site structure
            site_info = await explore_site_structure(page)
            
            # Find total number of pages
            total_pages = 1
            try:
                if site_info["pagination"]:
                    # Extract numbers from pagination text
                    numbers = re.findall(r'\d+', site_info["pagination"])
                    if len(numbers) >= 2:
                        total_pages = int(numbers[-1])  # Last number is usually total pages
                
                if total_pages == 1:
                    # Try alternate method - look for page numbers in navigation
                    page_numbers = await page.evaluate("""() => {
                        const elements = document.querySelectorAll('a[href*="page="]');
                        return Array.from(elements)
                            .map(el => {
                                const match = el.href.match(/page=(\\d+)/);
                                return match ? parseInt(match[1]) : 0;
                            })
                            .filter(n => n > 0);
                    }""")
                    if page_numbers:
                        total_pages = max(page_numbers)
            except Exception as e:
                print(f"⚠️ Could not determine total pages: {e}")
                total_pages = 100  # Default to reasonable limit
            
            # Set the output file path
            if output_dir:
                output_file = Path(output_dir) / f"{book_name}.txt"
            else:
                output_file = Path(f"{book_name}.txt")
            
            print(f"\n📖 Extracting '{book_name}' with {total_pages} pages...\n")
            
            # Determine the best selector for content based on site structure
            content_selectors = []
            for key, info in site_info["content_structure"].items():
                if info["hasText"]:
                    if key == "articles":
                        content_selectors.append("article")
                    elif key == "bookContent":
                        content_selectors.append("div.book-content, div.content")
                    elif key == "pageContent":
                        content_selectors.append("div.page-content")
                    elif key == "mainContent":
                        content_selectors.append("main, div.main-content")
                    elif key == "textContent":
                        content_selectors.append("div.text-content, div[class*='text']")
            
            if not content_selectors:
                content_selectors = ["article", "div.content", "div.book-content", "div.page-content"]
            
            content_selector = ", ".join(content_selectors)
            print(f"📄 Using selector: {content_selector}")
            
            all_text = []
            retry_count = 0
            for i in range(1, total_pages + 1):
                # Progress display
                progress = i * 20 // total_pages
                print(f"[{'=' * progress}{' ' * (20-progress)}] Page {i}/{total_pages}", end='\r')
                
                # Navigate to page
                page_url = re.sub(r'page=\d+', f'page={i}', url)
                if 'page=' not in page_url:
                    separator = '&' if '?' in page_url else '?'
                    page_url = f"{page_url}{separator}page={i}"
                
                try:
                    await page.goto(page_url, timeout=30000)
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(0.5)  # Short wait to let JS execute
                    
                    # Try to extract content
                    content = await page.evaluate(f"""() => {{
                        const elements = document.querySelectorAll('{content_selector}');
                        if (elements.length === 0) return '';
                        
                        // Filter out elements with no meaningful content
                        const validElements = Array.from(elements).filter(el => {{
                            const text = el.textContent.trim();
                            return text.length > 50;  // Minimum content length
                        }});
                        
                        return validElements.map(el => el.textContent.trim()).join('\\n\\n');
                    }}""")
                    
                    if content and content.strip():
                        all_text.append(content.strip())
                        retry_count = 0  # Reset retry counter on success
                    else:
                        print(f"\n⚠️ No content found on page {i}")
                        if retry_count < 3:  # Try a few times before giving up
                            print("Retrying...")
                            retry_count += 1
                            i -= 1  # Retry the same page
                            await asyncio.sleep(1)  # Wait a bit before retry
                            continue
                        
                except Exception as e:
                    print(f"\n❌ Error accessing page {i}: {e}")
                    if retry_count < 3:
                        print("Retrying...")
                        retry_count += 1
                        i -= 1
                        await asyncio.sleep(1)
                        continue
                    
                retry_count = 0  # Reset retry counter for next page
            
            await browser.close()
            
            # Write to file
            if all_text:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write('\n\n'.join(all_text))
                print(f"\n\n✅ Book saved to {output_file}")
                return str(output_file)
            else:
                print("\n❌ No content extracted. Check URL and website structure.")
                return None
                
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(
        description="Extract all pages of an Arabic book from ketabonline.com and save as a .txt file."
    )
    parser.add_argument('url', nargs='?', help='The URL of the book (start from page=1)')
    parser.add_argument('-o', '--output-dir', help='Directory to save the output file')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show verbose output')
    args = parser.parse_args()

    if not args.url:
        print("\n📚 Arabic Book Extractor 📚")
        print("\nPlease enter the book URL (e.g., https://ketabonline.com/ar/books/1113/read?part=1&page=1):")
        url = input('> ').strip()
    else:
        url = args.url

    if not url or 'ketabonline.com' not in url:
        print("❌ Please provide a valid ketabonline.com book URL.")
        sys.exit(1)

    # Show start time
    start_time = datetime.now()
    print(f"\n🕒 Started extraction at {start_time.strftime('%H:%M:%S')}")
    
    # Run the extraction
    output_file = asyncio.run(extract_book(url, args.output_dir))
    
    # Show completion time
    end_time = datetime.now()
    duration = end_time - start_time
    print(f"⏱️ Extraction completed in {duration.seconds} seconds")
    
    if output_file:
        print(f"📁 File size: {os.path.getsize(output_file) / 1024:.2f} KB")

if __name__ == '__main__':
    main() 