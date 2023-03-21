import os
import re
from tqdm import tqdm
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import openai
import trafilatura
import requests

ALLOWED_FILE_TYPES = [".txt", ".md", ".rtf", ".html"]


class ChatGPT:

    def __init__(self, key, target_language, not_to_translate_people_names):
        self.key = key
        self.target_language = target_language
        self.last_request_time = 0
        self.request_interval = 1  # seconds
        self.max_backoff_time = 60  # seconds
        self.not_to_translate_people_names = not_to_translate_people_names

    def translate(self, text):
        # Set up OpenAI API key
        openai.api_key = self.key
        if not text:
            return ""
        # lang
        while True:
            try:
                # Check if enough time has passed since the last request
                elapsed_time = time.monotonic() - self.last_request_time
                if elapsed_time < self.request_interval:
                    time.sleep(self.request_interval - elapsed_time)
                self.last_request_time = time.monotonic()
                # change prompt based on not_to_translate_people_names
                if self.not_to_translate_people_names:
                    completion = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[{
                            'role': 'system',
                            'content': 'You are a translator assistant.'
                        }, {
                            "role":
                            "user",
                            "content":
                            f"Translate the following text into {self.target_language} in a way that is faithful to the original text. But do not translate people and authors' names and surnames. Return only the translation and nothing else:\n{text}",
                        }],
                    )
                else:
                    completion = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[{
                            'role': 'system',
                            'content': 'You are a translator assistant who is expertly fluent in both English and Spanish, and are a native speaker of both languages.'
                        }, {
                            "role":
                            "user",
                            "content":
                            f"Translate the following text into {self.target_language} in a way that is faithful to the original text. Return only the translation and nothing else:\n{text}",
                        }],
                    )
                t_text = (completion["choices"][0].get("message").get(
                    "content").encode("utf8").decode())
                break
            except Exception as e:
                print(str(e))
                # Exponential backoff if rate limit is hit
                self.request_interval *= 2
                if self.request_interval > self.max_backoff_time:
                    self.request_interval = self.max_backoff_time
                print(
                    f"Rate limit hit. Sleeping for {self.request_interval} seconds."
                )
                time.sleep(self.request_interval)
                continue

        return t_text


def translate_text_file(text_filepath_or_url, options):
    OPENAI_API_KEY = options.openai_key or os.environ.get("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")
    translator = ChatGPT(OPENAI_API_KEY, options.target_language,
                         options.not_to_translate_people_names)

    paragraphs = read_and_preprocess_data(text_filepath_or_url)

    # keep first three paragraphs
    first_three_paragraphs = paragraphs[:2]

    # if users require to ignore References, we then take out all paragraphs after the one starting with "References"

    if options.not_to_translate_references:
        ignore_strings = ["Acknowledgment", "Notes", "NOTES", "disclosure statement", "References", "Funding", "declaration of conflicting interest", "acknowledgment", "supplementary material", "Acknowledgements"]
        ignore_indices = []
        for i, p in enumerate(paragraphs):
            for ignore_str in ignore_strings:
                if (p.startswith(ignore_str) or p.lower().startswith(ignore_str.lower())) and len(p) < 30:
                    ignore_indices.append(i)
                    break
        if ignore_indices:
            print("References will not be translated.")
            ref_paragraphs = paragraphs[min(ignore_indices):]
            paragraphs = paragraphs[:min(ignore_indices)]
        else:
            print(paragraphs[-3:])
            raise Exception("No References found.")


    def split_and_translate(paragraph):
        import nltk
        try:
            nltk.data.find('tokenizers/punkt')
        except:
            nltk.download('punkt')
        from nltk.tokenize import sent_tokenize
        
        words = paragraph.split()
        if len(words) > 10000:
            sentences = sent_tokenize(paragraph)
            half = len(sentences) // 2
            first_half = " ".join(sentences[:half])
            second_half = " ".join(sentences[half:])
            translated_first_half = translator.translate(first_half).strip()
            translated_second_half = translator.translate(second_half).strip()
            return translated_first_half + " " + translated_second_half
        else:
            return translator.translate(paragraph).strip()

    with ThreadPoolExecutor(max_workers=options.num_threads) as executor:
        translated_paragraphs = list(
            tqdm(executor.map(split_and_translate, paragraphs),
                    total=len(paragraphs),
                    desc="Translating paragraphs",
                    unit="paragraph"))

    translated_text = "\n".join(translated_paragraphs)

    if options.bilingual:
        bilingual_text = "\n".join(f"{paragraph}\n{translation}"
                                   for paragraph, translation in zip(
                                      paragraphs, translated_paragraphs))
        # add first three paragraphs if required
        if options.keep_first_two_paragraphs:
            bilingual_text = "\n".join(
                first_three_paragraphs) + "\n" + bilingual_text
        # append References
        if options.not_to_translate_references:
            bilingual_text += "\n".join(ref_paragraphs)
        output_file = f"{Path(text_filepath_or_url).parent}/{Path(text_filepath_or_url).stem}_bilingual.txt"
        with open(output_file, "w") as f:
            f.write(bilingual_text)
            print(f"Bilingual text saved to {f.name}.")
    else:
        # remove extra newlines
        translated_text = re.sub(r"\n{2,}", "\n", translated_text)
        # add first three paragraphs if required
        if options.keep_first_two_paragraphs:
            translated_text = "\n".join(
                first_three_paragraphs) + "\n" + translated_text
        # append References
        if options.not_to_translate_references:
            translated_text += "\n" + "\n".join(ref_paragraphs)
        output_file = f"{Path(text_filepath_or_url).parent}/{Path(text_filepath_or_url).stem}_translated.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(translated_text)
            print(f"Translated text saved to {f.name}.")


def download_html(url):
    response = requests.get(url)
    return response.text


def read_and_preprocess_data(text_filepath_or_url):
    if text_filepath_or_url.startswith('http'):
        # replace "https:/www" with "https://www"
        text_filepath_or_url = text_filepath_or_url.replace(":/", "://")
        # download and extract text from URL
        print("Downloading and extracting text from URL...")
        downloaded = trafilatura.fetch_url(text_filepath_or_url)
        print("Downloaded text:")
        print(downloaded)
        text = trafilatura.extract(downloaded)
    else:
        with open(text_filepath_or_url, "r", encoding='utf-8') as f:
            text = f.read()
            if text_filepath_or_url.endswith('.html'):
                # extract text from HTML file
                print("Extracting text from HTML file...")
                text = trafilatura.extract(text)
                # write to a txt file ended with "_extracted"
                with open(
                        f"{Path(text_filepath_or_url).parent}/{Path(text_filepath_or_url).stem}_extracted.txt",
                        "w") as f:
                    f.write(text)
                    print(f"Extracted text saved to {f.name}.")
    paragraphs = [p.strip() for p in text.split("\n") if p.strip() != ""]
    return paragraphs


def parse_arguments():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input_path",
        dest="input_path",
        type=str,
        help="input file or folder to translate",
    )
    parser.add_argument(
        "--openai_key",
        dest="openai_key",
        type=str,
        default="",
        help="OpenAI API key",
    )
    parser.add_argument(
        "--num_threads",
        dest="num_threads",
        type=int,
        default=10,
        help="number of threads to use for translation",
    )
    parser.add_argument(
        "--bilingual",
        dest="bilingual",
        action="store_true",
        default=False,
        help=
        "output bilingual txt file with original and translated text side by side",
    )
    parser.add_argument(
        "--target_language",
        dest="target_language",
        type=str,
        default="Spanish",
        help="target language to translate to",
    )

    parser.add_argument(
        "--not_to_translate_people_names",
        dest="not_to_translate_people_names",
        action="store_true",
        default=False,
        help="whether or not to translate names in the text",
    )
    parser.add_argument(
        "--not_to_translate_references",
        dest="not_to_translate_references",
        action="store_true",
        default=False,
        help="not to translate references",
    )
    parser.add_argument(
        "--keep_first_two_paragraphs",
        dest="keep_first_two_paragraphs",
        action="store_true",
        default=False,
        help="keep the first three paragraphs of the original text",
    )
    # add arg: only_process_this_file_extension
    parser.add_argument(
        "--only_process_this_file_extension",
        dest="only_process_this_file_extension",
        type=str,
        default="",
        help="only process files with this extension",
    )

    options = parser.parse_args()
    OPENAI_API_KEY = options.openai_key or os.environ.get("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")
    if not OPENAI_API_KEY:
        raise Exception("Please provide your OpenAI API key")
    return options


def check_file_path(file_path: Path, options=None):
    """
    Ensure file extension is in ALLOWED_FILE_TYPES or is a URL.
    If file ends with _translated.txt or _bilingual.txt, skip it.
    If there is any txt file ending with _translated.txt or _bilingual.txt, skip it.
    """
    if not file_path.suffix.lower() in ALLOWED_FILE_TYPES and not str(
            file_path).startswith('http'):
        raise Exception("Please use a txt file or URL")

    if file_path.stem.endswith("_translated") or file_path.stem.endswith(
            "extracted_translated"):
        print(
            f"You already have a translated file for {file_path}, skipping...")
        return False
    elif file_path.stem.endswith("_bilingual") or file_path.stem.endswith(
            "extracted_bilingual"):
        print(
            f"You already have a bilingual file for {file_path}, skipping...")
        return False

    if (file_path.with_name(f"{file_path.stem}_translated.txt").exists() or
            file_path.with_name(f"{file_path.stem}_extracted_translated.txt").
            exists()) and not getattr(options, 'bilingual', False):
        print(
            f"You already have a translated file for {file_path}, skipping...")
        return False
    elif (file_path.with_name(f"{file_path.stem}_bilingual.txt").exists()
          or file_path.with_name(f"{file_path.stem}_extracted_bilingual.txt").
          exists()) and getattr(options, 'bilingual', False):
        print(
            f"You already have a bilingual file for {file_path}, skipping...")
        return False

    return True


def process_file(file_path, options):
    """Translate a single text file"""
    if not check_file_path(file_path, options):
        return
    print(f"Translating {file_path}...")
    translate_text_file(str(file_path), options)


def process_folder(folder_path, options):
    """Translate all text files in a folder"""
    # if only_process_this_file_extension is set, only process files with this extension
    if options.only_process_this_file_extension:
        files_to_process = list(
            folder_path.rglob(f"*.{options.only_process_this_file_extension}"))
        print(
            f"Only processing files with extension {options.only_process_this_file_extension}"
        )
        print(f"Found {len(files_to_process)} files to process")
    else:
        files_to_process = list(folder_path.rglob("*"))
    total_files = len(files_to_process)
    for index, file_path in enumerate(files_to_process):
        if file_path.is_file() and file_path.suffix.lower(
        ) in ALLOWED_FILE_TYPES:
            process_file(file_path, options)
        print(
            f"Processed file {index + 1} of {total_files}. Only {total_files - index - 1} files left to process."
        )


def main():
    """Main function"""
    options = parse_arguments()
    input_path = Path(options.input_path)
    if input_path.is_dir():
        # input path is a folder, scan and process all allowed file types
        process_folder(input_path, options)
    elif input_path.is_file:
        process_file(input_path, options)


if __name__ == "__main__":
    main()
