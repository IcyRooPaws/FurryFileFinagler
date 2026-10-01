"""
Main file sorting logic that orchestrates the file processing workflow.
"""
import time
import logging
from .utils import (is_video, is_image, is_gif, is_explicit, is_questionable, 
                    is_explicit_or_adult, is_questionable_or_mature, is_sfw_or_general)

logger = logging.getLogger(__name__)

class FileSorter:
    """Main file sorting orchestrator."""
    
    def __init__(self, config, db_manager, file_operations, artist_manager, hash_manager):
        self.config = config
        self.db_manager = db_manager
        self.file_operations = file_operations
        self.artist_manager = artist_manager
        self.hash_manager = hash_manager
        self.source_directories = config.source_directories
        self.destination_directories = config.destination_directories
    
    def start_processing(self):
        """Main processing function that orchestrates the entire sorting workflow."""
        start_time = time.time()
        total_files_moved = 0
        total_duplicates_moved = 0

        # 1. Move files from special category directories first
        print("Processing special category directories...")
        nsfw_moved = self.file_operations.move_special_category_dirs(self.source_directories)
        if nsfw_moved > 0:
            print(f"   Moved {nsfw_moved} files from 'adult'/'mature' to nsfw_image")
        total_files_moved += nsfw_moved

        # 2. Move favorite files from source directories
        print("Moving favorite files from source directories...")
        fav_moved = self.artist_manager.move_favorite_files(self.source_directories)
        if fav_moved > 0:
            print(f"   Moved {fav_moved} favorite files")
        total_files_moved += fav_moved

        # 3. Move FurAffinity files ('-FA-' in filename) into rating-based
        #    "Furaffinity - {Rating}" subfolders before generic rating sorting claims them
        print("Processing FurAffinity files...")
        fa_moved, fa_dup = self.file_operations.move_furaffinity_files(self.source_directories)
        if fa_moved > 0 or fa_dup > 0:
            print(f"   FurAffinity files: {fa_moved} moved, {fa_dup} duplicates")
        total_files_moved += fa_moved
        total_duplicates_moved += fa_dup

        # 4. Process files by type and rating sequentially
        print("Processing files by type and rating...")
        
        # NSFW videos (includes explicit, adult, questionable, mature)
        moved, dup = self.file_operations.process_directories(
            self.source_directories,
            lambda file: is_video(file) and is_explicit_or_adult(file),
            self.destination_directories['nsfw_video']
        )
        if moved > 0 or dup > 0:
            print(f"   NSFW videos: {moved} moved, {dup} duplicates")
        total_files_moved += moved
        total_duplicates_moved += dup

        # NSFW gifs (includes explicit, adult, questionable, mature)
        moved, dup = self.file_operations.process_directories(
            self.source_directories,
            lambda file: is_gif(file) and is_explicit_or_adult(file),
            self.destination_directories['nsfw_gif']
        )
        if moved > 0 or dup > 0:
            print(f"   NSFW gifs: {moved} moved, {dup} duplicates")
        total_files_moved += moved
        total_duplicates_moved += dup

        # NSFW images (includes explicit, adult, questionable, mature)
        moved, dup = self.file_operations.process_directories(
            self.source_directories,
            lambda file: is_image(file) and is_explicit_or_adult(file),
            self.destination_directories['nsfw_image']
        )
        if moved > 0 or dup > 0:
            print(f"   NSFW images: {moved} moved, {dup} duplicates")
        total_files_moved += moved
        total_duplicates_moved += dup

        # 5. Process SFW files by type
        # SFW videos
        moved, dup = self.file_operations.process_directories(
            self.source_directories,
            lambda file: is_video(file) and is_sfw_or_general(file),
            self.destination_directories['sfw_video']
        )
        if moved > 0 or dup > 0:
            print(f"   SFW videos: {moved} moved, {dup} duplicates")
        total_files_moved += moved
        total_duplicates_moved += dup

        # SFW gifs
        moved, dup = self.file_operations.process_directories(
            self.source_directories,
            lambda file: is_gif(file) and is_sfw_or_general(file),
            self.destination_directories['sfw_gif']
        )
        if moved > 0 or dup > 0:
            print(f"   SFW gifs: {moved} moved, {dup} duplicates")
        total_files_moved += moved
        total_duplicates_moved += dup

        # SFW images
        sfw_moved = self.file_operations.move_sfw_images_to_sfw_dir(self.source_directories)
        if sfw_moved > 0:
            print(f"   SFW images: {sfw_moved} moved")
        total_files_moved += sfw_moved

        # 6. Move remaining files to manual sort
        manual_moved = self.file_operations.move_remaining_files_to_manual_sort(self.source_directories)
        if manual_moved > 0:
            print(f"   Manual sort: {manual_moved} files")
        total_files_moved += manual_moved

        # Calculate elapsed time
        elapsed_time = time.time() - start_time
        
        print(f"\n[OK] Sorting complete: {total_files_moved} files moved, {total_duplicates_moved} duplicates ({elapsed_time:.1f}s)")

        # Log final results
        logger.info(f"Processing complete - Files moved: {total_files_moved}, Duplicates: {total_duplicates_moved}, Time: {elapsed_time:.2f}s")
        
        return {
            'files_moved': total_files_moved,
            'duplicates_moved': total_duplicates_moved,
            'elapsed_time': elapsed_time
        }
