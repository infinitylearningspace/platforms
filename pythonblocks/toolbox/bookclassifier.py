#!/usr/bin/env python3
"""
Book Genre Classifier Script
Processes CSV files to determine book genres and add subtags.
"""

import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
import re
import json
import sys
import os
from urllib.parse import quote_plus
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BookGenreClassifier:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        # Genre mapping to subtags
        self.genre_mapping = {
            'self help': 'sh',
            'self-help': 'sh',
            'leadership': 'sh',
            'personal development': 'sh',
            'business': 'sh',
            'motivation': 'sh',
            'comic': 'comics',
            'comics': 'comics',
            'graphic novel': 'comics',
            'manga': 'comics',
            'novel': 'novel',
            'fiction': 'novel',
            'romance': 'novel',
            'mystery': 'novel',
            'thriller': 'novel',
            'fantasy': 'novel',
            'science fiction': 'novel',
            'historical fiction': 'novel',
            'literary fiction': 'novel',
            'young adult': 'novel',
            'unknown': 'unknown'
        }

    def validate_csv_columns(self, df):
        """Check if required columns are present in the DataFrame"""
        required_columns = ['title', 'creators', 'ean_isbn13', 'upc_isbn10']
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            logger.error(f"Missing required columns: {missing_columns}")
            return False
        return True

    def clean_isbn(self, isbn):
        """Clean and validate ISBN"""
        if pd.isna(isbn) or isbn == '':
            return None

        # Remove any non-digit characters except hyphens
        isbn = re.sub(r'[^\d\-X]', '', str(isbn))
        # Remove hyphens
        isbn = isbn.replace('-', '')

        return isbn if len(isbn) in [10, 13] else None

    def search_google_books(self, title, author, isbn):
        """Search Google Books API for genre information"""
        try:
            # Try ISBN first
            if isbn:
                url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
            else:
                # Fallback to title and author
                query = f"{title} {author}".strip()
                url = f"https://www.googleapis.com/books/v1/volumes?q={quote_plus(query)}"

            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if 'items' in data and len(data['items']) > 0:
                book = data['items'][0]['volumeInfo']
                categories = book.get('categories', [])
                if categories:
                    return ', '.join(categories).lower()

        except Exception as e:
            logger.debug(f"Google Books search failed: {e}")

        return None

    def search_openlibrary(self, title, author, isbn):
        """Search Open Library for genre information"""
        try:
            if isbn:
                url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
            else:
                return None

            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            for key, book_data in data.items():
                if 'subjects' in book_data:
                    subjects = [subject['name'].lower() for subject in book_data['subjects']]
                    return ', '.join(subjects)

        except Exception as e:
            logger.debug(f"Open Library search failed: {e}")

        return None

    def search_goodreads_scrape(self, title, author):
        """Attempt to scrape Goodreads for genre information"""
        try:
            query = f"{title} {author}".strip()
            url = f"https://www.goodreads.com/search?q={quote_plus(query)}"

            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Look for genre/shelf information in the search results
            genre_elements = soup.find_all(['span', 'div'], class_=re.compile(r'genre|shelf|tag', re.I))
            genres = []

            for element in genre_elements:
                text = element.get_text(strip=True).lower()
                if any(keyword in text for keyword in
                       ['fiction', 'romance', 'fantasy', 'mystery', 'thriller', 'self-help', 'business']):
                    genres.append(text)

            if genres:
                return ', '.join(genres[:3])  # Limit to first 3 genres

        except Exception as e:
            logger.debug(f"Goodreads scraping failed: {e}")

        return None

    def determine_genre(self, title, author, isbn13, isbn10):
        """Determine genre using multiple sources"""
        logger.info(f"Processing: {title} by {author}")

        # Clean ISBNs
        clean_isbn13 = self.clean_isbn(isbn13)
        clean_isbn10 = self.clean_isbn(isbn10)

        # Try different sources
        sources = [
            ('Google Books (ISBN13)', lambda: self.search_google_books(title, author, clean_isbn13)),
            ('Google Books (ISBN10)', lambda: self.search_google_books(title, author, clean_isbn10)),
            ('Google Books (Title/Author)', lambda: self.search_google_books(title, author, None)),
            ('Open Library (ISBN13)', lambda: self.search_openlibrary(title, author, clean_isbn13)),
            ('Open Library (ISBN10)', lambda: self.search_openlibrary(title, author, clean_isbn10)),
            ('Goodreads', lambda: self.search_goodreads_scrape(title, author))
        ]

        for source_name, search_func in sources:
            try:
                result = search_func()
                if result:
                    logger.info(f"Found genre from {source_name}: {result}")
                    return result
                time.sleep(1)  # Be respectful to APIs
            except Exception as e:
                logger.debug(f"{source_name} failed: {e}")
                continue

        logger.warning(f"No genre found for: {title}")
        return 'unknown'

    def map_genre_to_subtag(self, genre_text):
        """Map genre text to subtag"""
        if not genre_text or genre_text == 'unknown':
            return 'unknown'

        genre_text = genre_text.lower()

        # Check for exact matches first
        for genre, subtag in self.genre_mapping.items():
            if genre in genre_text:
                return subtag

        # Default to unknown if no match found
        return 'unknown'

    def process_csv(self, input_file):
        """Process the CSV file"""
        try:
            # Read CSV
            logger.info(f"Reading CSV file: {input_file}")
            df = pd.read_csv(input_file)
            logger.info(f"Found {len(df)} rows")

            # Validate columns
            if not self.validate_csv_columns(df):
                return False

            # Add subtag column
            df['subtag'] = 'unknown'

            # Create unprocessed books list
            unprocessed_books = []

            # Process each row
            for index, row in df.iterrows():
                try:
                    title = str(row['title']) if pd.notna(row['title']) else ''
                    author = str(row['creators']) if pd.notna(row['creators']) else ''
                    isbn13 = row['ean_isbn13'] if pd.notna(row['ean_isbn13']) else ''
                    isbn10 = row['upc_isbn10'] if pd.notna(row['upc_isbn10']) else ''

                    if not title.strip():
                        logger.warning(f"Row {index + 1}: No title found, skipping")
                        unprocessed_books.append({
                            'row': index + 1,
                            'title': title,
                            'creators': author,
                            'ean_isbn13': isbn13,
                            'upc_isbn10': isbn10,
                            'reason': 'No title'
                        })
                        continue

                    # Determine genre
                    genre = self.determine_genre(title, author, isbn13, isbn10)

                    # Map to subtag
                    subtag = self.map_genre_to_subtag(genre)
                    df.at[index, 'subtag'] = subtag

                    logger.info(f"Row {index + 1}: {title} -> {subtag}")

                    if subtag == 'unknown':
                        unprocessed_books.append({
                            'row': index + 1,
                            'title': title,
                            'creators': author,
                            'ean_isbn13': isbn13,
                            'upc_isbn10': isbn10,
                            'reason': 'Genre not found'
                        })

                    # Add delay to be respectful to servers
                    time.sleep(2)

                except Exception as e:
                    logger.error(f"Error processing row {index + 1}: {e}")
                    unprocessed_books.append({
                        'row': index + 1,
                        'title': row.get('title', ''),
                        'creators': row.get('creators', ''),
                        'ean_isbn13': row.get('ean_isbn13', ''),
                        'upc_isbn10': row.get('upc_isbn10', ''),
                        'reason': f'Processing error: {str(e)}'
                    })
                    continue

            # Save processed file
            base_name = os.path.splitext(input_file)[0]
            output_file = f"{base_name}_processed.csv"
            df.to_csv(output_file, index=False)
            logger.info(f"Processed file saved as: {output_file}")

            # Save unprocessed books
            if unprocessed_books:
                unprocessed_file = f"{base_name}_unprocessed.csv"
                unprocessed_df = pd.DataFrame(unprocessed_books)
                unprocessed_df.to_csv(unprocessed_file, index=False)
                logger.info(f"Unprocessed books saved as: {unprocessed_file}")
                logger.info(f"Total unprocessed books: {len(unprocessed_books)}")

            return True

        except Exception as e:
            logger.error(f"Error processing CSV: {e}")
            return False


def main():
    if len(sys.argv) != 2:
        print("Usage: python book_genre_classifier.py <csv_file>")
        print("\nThis script processes a CSV file with book information and adds genre subtags.")
        print("\nRequired columns:")
        print("  - title: Book title")
        print("  - creators: Author name")
        print("  - ean_isbn13: 13-digit ISBN")
        print("  - upc_isbn10: 10-digit ISBN")
        print("\nOutput:")
        print("  - [input_file]_processed.csv: Original file with added 'subtag' column")
        print("  - [input_file]_unprocessed.csv: List of books that couldn't be processed")
        sys.exit(1)

    input_file = sys.argv[1]

    if not os.path.exists(input_file):
        logger.error(f"File not found: {input_file}")
        sys.exit(1)

    classifier = BookGenreClassifier()
    success = classifier.process_csv(input_file)

    if success:
        logger.info("Processing completed successfully!")
    else:
        logger.error("Processing failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()