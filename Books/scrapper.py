"""
Arabic Book Extractor for ketabonline.com

A script to extract the full content of Arabic books from ketabonline.com
"""

import asyncio
import re
import sys
import os
from pathlib import Path
from playwright.async_api import async_playwright
import argparse
from datetime import datetime
from tqdm import tqdm  # For progress bar
import time
import requests
from bs4 import BeautifulSoup

class KetabOnlineExtractor:
    """Main class for extracting books from ketabonline.com"""
    
    def __init__(self, book_id, book_name, max_retries=3, delay=1):
        """
        Initialize the extractor with book information
        
        Args:
            book_id (str): The ID of the book on ketabonline.com
            book_name (str): The name of the book for the output file
            max_retries (int): Maximum number of retry attempts for failed requests
            delay (float): Delay between requests to avoid rate limiting
        """
        self.base_url = f"https://ketabonline.com/ar/books/{book_id}/read"
        self.book_id = book_id
        self.book_name = book_name
        self.max_retries = max_retries
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        })
        self.total_pages = 0
        
    def get_page(self, page_number, part=1):
        """
        Get the content of a specific page
        
        Args:
            page_number (int): The page number to extract
            part (int): The part number (default: 1)
            
        Returns:
            str or None: The page content or None if failed
        """
        url = f"{self.base_url}?part={part}&page={page_number}"
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, timeout=10)
                
                if response.status_code == 200:
                    return response.text
                    
                print(f"Error {response.status_code} for page {page_number}. Retrying ({attempt+1}/{self.max_retries})...")
                time.sleep(self.delay * (attempt + 1))  # Exponential backoff
                
            except requests.RequestException as e:
                print(f"Request error for page {page_number}: {e}. Retrying ({attempt+1}/{self.max_retries})...")
                time.sleep(self.delay * (attempt + 1))
        
        print(f"Failed to get page {page_number} after {self.max_retries} attempts.")
        return None
    
    def get_total_pages(self):
        """
        Get the total number of pages in the book
        
        Returns:
            int: Total number of pages
        """
        html = self.get_page(1)
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
        
        # Find the main article content which contains the actual book text
        articles = soup.select('article')
        
        # If the article elements exist, extract and clean the content
        content = []
        for article in articles:
            # Remove footnote links, navigation buttons, etc.
            for el in article.select('.footnote, .nav-btn, .page-controls, script, style'):
                el.decompose()
                
            # Get paragraphs of text
            paragraphs = article.select('p')
            for paragraph in paragraphs:
                # Get the text and clean it
                text = paragraph.get_text(strip=True)
                if text and not text.isdigit():  # Skip page numbers or empty paragraphs
                    content.append(text)
                    
        # If no articles found or no content extracted, try looking for text in other elements
        if not content:
            paragraphs = soup.select('.article p, .content p, .book-content p, .page-container p')
            for paragraph in paragraphs:
                text = paragraph.get_text(strip=True)
                if text and not text.isdigit():
                    content.append(text)
        
        return "\n\n".join(content)
    
    def extract_book(self):
        """
        Extract the complete book content
        
        Returns:
            str: The complete book content
        """
        try:
            self.total_pages = self.get_total_pages()
            print(f"Book has {self.total_pages} pages.")
            
            all_content = []
            for page in tqdm(range(1, self.total_pages + 1), desc="Extracting pages"):
                html = self.get_page(page)
                content = self.extract_content_from_html(html)
                
                if content:
                    all_content.append(content)
                else:
                    print(f"Warning: No content extracted from page {page}")
                
                # Avoid hammering the server
                time.sleep(self.delay)
                
            return "\n\n===========\n\n".join(all_content)
            
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
    
    def extract_and_save(self):
        """
        Extract the book content and save it to a file
        
        Returns:
            bool: True if successful, False otherwise
        """
        content = self.extract_book()
        if content:
            return self.save_to_file(content)
        return False


# Main execution
if __name__ == "__main__":
    print("\n📚 Arabic Book Extractor 📚\n")
    
    # For صيد الخاطر book
    book_id = "1113"
    book_name = "صيد_الخاطر"
    
    print(f"Extracting book: صيد الخاطر (ID: {book_id})")
    extractor = KetabOnlineExtractor(book_id, book_name)
    extractor.extract_and_save() 