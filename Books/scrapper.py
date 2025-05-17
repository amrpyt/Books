"""
Arabic Book Extractor for ketabonline.com

A script to extract the full content of Arabic books from ketabonline.com
"""

import asyncio
import re
import os
from pathlib import Path
import aiohttp
from bs4 import BeautifulSoup
from tqdm import tqdm
import time

class KetabOnlineExtractor:
    """Main class for extracting books from ketabonline.com"""
    
    def __init__(self, book_id, book_name, max_retries=3, delay=0.1, concurrency=10):
        """
        Initialize the extractor with book information
        
        Args:
            book_id (str): The ID of the book on ketabonline.com
            book_name (str): The name of the book for the output file
            max_retries (int): Maximum number of retry attempts for failed requests
            delay (float): Delay between requests to avoid rate limiting
            concurrency (int): Maximum number of concurrent requests
        """
        self.base_url = f"https://ketabonline.com/ar/books/{book_id}/read"
        self.book_id = book_id
        self.book_name = book_name
        self.max_retries = max_retries
        self.delay = delay
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
        self.total_pages = 0
        
    async def get_page(self, session, page_number, part=1):
        """
        Get the content of a specific page asynchronously
        
        Args:
            session (aiohttp.ClientSession): The aiohttp session
            page_number (int): The page number to extract
            part (int): The part number (default: 1)
            
        Returns:
            tuple: (page_number, html_content) or (page_number, None) if failed
        """
        url = f"{self.base_url}?part={part}&page={page_number}"
        
        async with self.semaphore:  # Limit concurrency
            for attempt in range(self.max_retries):
                try:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            return page_number, await response.text()
                        
                    print(f"Error {response.status} for page {page_number}. Retrying ({attempt+1}/{self.max_retries})...")
                    await asyncio.sleep(self.delay * (attempt + 1))  # Exponential backoff
                    
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    print(f"Request error for page {page_number}: {e}. Retrying ({attempt+1}/{self.max_retries})...")
                    await asyncio.sleep(self.delay * (attempt + 1))
            
            print(f"Failed to get page {page_number} after {self.max_retries} attempts.")
            return page_number, None
    
    async def get_total_pages(self, session):
        """
        Get the total number of pages in the book asynchronously
        
        Returns:
            int: Total number of pages
        """
        _, html = await self.get_page(session, 1)
        if not html:
            raise Exception("Cannot retrieve first page to determine total pages")
            
        try:
            # Look for pagination information showing total pages
            soup = BeautifulSoup(html, 'html.parser')
            
            # Try first with pagination info selector
            pagination_text = None
            page_nav = soup.select_one('.page-nav')
            if page_nav:
                pagination_div = page_nav.select_one('div:contains("/")')
                if pagination_div:
                    pagination_text = pagination_div.text.strip()
            
            if not pagination_text:
                # Try with direct text search
                for div in soup.select('div'):
                    if '/' in div.text:
                        match = re.search(r'(\d+)\s*/\s*(\d+)', div.text)
                        if match:
                            pagination_text = div.text
                            break
            
            if pagination_text:
                match = re.search(r'(\d+)\s*/\s*(\d+)', pagination_text)
                if match:
                    return int(match.group(2))
        
        except Exception as e:
            print(f"Error determining total pages: {e}")
        
        # Fallback: Try to determine by checking TOC items
        try:
            toc_items = soup.select('.toc-item a')
            if toc_items:
                # Extract all page numbers from TOC links
                page_numbers = []
                for item in toc_items:
                    href = item.get('href', '')
                    page_match = re.search(r'page=(\d+)', href)
                    if page_match:
                        page_numbers.append(int(page_match.group(1)))
                
                if page_numbers:
                    return max(page_numbers)
                    
        except Exception as e:
            print(f"Error determining total pages from TOC: {e}")
        
        # Last resort: Check for a specific value we know about
        return 560  # Known number of pages for this book
    
    def extract_content_from_html(self, html):
        """
        Extract the book content from page HTML
        
        Args:
            html (str): The HTML content of the page
            
        Returns:
            str: The extracted text content
        """
        if not html:
            return ""
            
        soup = BeautifulSoup(html, 'html.parser')
        content = []
        
        # The main content is inside article elements nested in the generic container
        articles = soup.select('article')
        
        if articles:
            for article in articles:
                # Remove unwanted elements
                for el in article.select('.footnote, .nav-btn, .page-controls, script, style'):
                    el.decompose()
                
                # Extract text from paragraphs
                paragraphs = article.select('p')
                for paragraph in paragraphs:
                    # Skip elements that only contain references
                    if paragraph.select_one('a[href^="#"]') and len(paragraph.get_text(strip=True)) < 5:
                        continue
                        
                    # Skip page numbers
                    text = paragraph.get_text(strip=True)
                    if text and not re.match(r'^\d+$', text):
                        # Clean the text - remove page numbers and footnote marks
                        text = re.sub(r'\d+-', '', text)
                        content.append(text)
        
        # If no content was found with the above method, try more generic selectors
        if not content:
            # Try to find article text in any visible paragraph element
            paragraphs = soup.select('article p, .article-content p, .page-content p')
            for paragraph in paragraphs:
                text = paragraph.get_text(strip=True)
                if text and not re.match(r'^\d+$', text):
                    content.append(text)
        
        # If still no content, try directly selecting elements that are likely to contain the content
        if not content:
            # Look for content in the generic container where the book text is usually located
            content_container = soup.select_one('.generic')
            if content_container:
                paragraphs = content_container.select('p')
                for paragraph in paragraphs:
                    text = paragraph.get_text(strip=True)
                    if text and not re.match(r'^\d+$', text):
                        content.append(text)
        
        return "\n\n".join(content) if content else ""
    
    async def extract_book(self):
        """
        Extract the complete book content asynchronously
        
        Returns:
            str: The complete book content
        """
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                self.total_pages = await self.get_total_pages(session)
                print(f"Book has {self.total_pages} pages.")
                
                # Create tasks for all pages
                tasks = [self.get_page(session, page) for page in range(1, self.total_pages + 1)]
                
                # Initialize content list with None placeholders
                results = [None] * self.total_pages
                
                with tqdm(total=self.total_pages, desc="Downloading pages") as pbar:
                    # Process completed tasks as they come in
                    for future in asyncio.as_completed(tasks):
                        page_num, html = await future
                        if html:
                            content = self.extract_content_from_html(html)
                            results[page_num - 1] = content
                        else:
                            print(f"Warning: No HTML content for page {page_num}")
                        pbar.update(1)
                
                # Filter out any None values and join content
                valid_results = [r for r in results if r]
                return "\n\n===========\n\n".join(valid_results)
                
        except Exception as e:
            print(f"Error extracting book: {e}")
            return None
            
    def save_to_file(self, content):
        """
        Save the extracted content to a file
        
        Args:
            content (str): The content to save
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not content:
            return False
            
        filename = f"{self.book_name.replace(' ', '_')}.txt"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"Book saved to {filename}")
            return True
        except Exception as e:
            print(f"Error saving to file: {e}")
            return False
    
    async def extract_and_save(self):
        """
        Extract the book content and save it to a file
        
        Returns:
            bool: True if successful, False otherwise
        """
        content = await self.extract_book()
        if content:
            return self.save_to_file(content)
        return False


# Main execution
async def main():
    print("\n📚 Arabic Book Extractor 📚\n")
    
    # For صيد الخاطر book
    book_id = "1113"
    book_name = "صيد_الخاطر"
    concurrency = 15  # Number of concurrent requests
    
    print(f"Extracting book: صيد الخاطر (ID: {book_id}) with {concurrency} concurrent connections")
    extractor = KetabOnlineExtractor(book_id, book_name, concurrency=concurrency)
    await extractor.extract_and_save()

if __name__ == "__main__":
    asyncio.run(main()) 