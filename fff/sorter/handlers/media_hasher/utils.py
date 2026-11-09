# fff: old v1 hasher package, kept verbatim: DatabaseManager._get_hash_metadata uses its type
# fff: mapping for the hash_method/hash_type columns, and 'fff hash' wraps its CLI.
"""
Utility functions for MediaHasher - helper functions for common use cases
"""

from pathlib import Path
from typing import List, Dict, Set, Optional, Any
from collections import defaultdict

from .core import MediaHasher


def batch_hash_directory(directory_path: str | Path, 
                        recursive: bool = True,
                        file_extensions: Optional[Set[str]] = None,
                        **hasher_kwargs) -> Dict[str, Optional[str]]:
    """
    Hash all files in a directory
    
    Args:
        directory_path: Path to directory to scan
        recursive: Whether to scan subdirectories (default: True)
        file_extensions: Set of extensions to include (e.g., {'.jpg', '.png'})
                        If None, all files are included
        **hasher_kwargs: Additional arguments passed to MediaHasher
    
    Returns:
        Dictionary mapping file paths to their hashes
    """
    directory = Path(directory_path)
    if not directory.exists() or not directory.is_dir():
        raise ValueError(f"Directory does not exist: {directory_path}")
    
    hasher = MediaHasher(**hasher_kwargs)
    results = {}
    
    # Choose iteration method based on recursive flag
    if recursive:
        pattern = "**/*"
        files = directory.glob(pattern)
    else:
        files = directory.iterdir()
    
    for file_path in files:
        if file_path.is_file():
            # Filter by extension if specified
            if file_extensions is not None:
                if file_path.suffix.lower() not in file_extensions:
                    continue
            
            results[str(file_path)] = hasher.hash_file(file_path)
    
    return results


def find_duplicates(file_hashes: Dict[str, Optional[str]]) -> Dict[str, List[str]]:
    """
    Find duplicate files based on their hashes
    
    Args:
        file_hashes: Dictionary mapping file paths to hashes
                    (output from batch_hash_directory or similar)
    
    Returns:
        Dictionary mapping hashes to lists of files with that hash
        Only includes hashes with multiple files
    """
    hash_to_files = defaultdict(list)
    
    # Group files by hash
    for file_path, file_hash in file_hashes.items():
        if file_hash is not None:  # Skip files that couldn't be hashed
            hash_to_files[file_hash].append(file_path)
    
    # Return only hashes with duplicates
    duplicates = {
        hash_value: files 
        for hash_value, files in hash_to_files.items() 
        if len(files) > 1
    }
    
    return duplicates


def compare_files(file1: str | Path, file2: str | Path, **hasher_kwargs) -> Dict[str, Any]:
    """
    Compare two files and return detailed comparison information
    
    Args:
        file1: Path to first file
        file2: Path to second file
        **hasher_kwargs: Additional arguments passed to MediaHasher
    
    Returns:
        Dictionary with comparison results
    """
    hasher = MediaHasher(**hasher_kwargs)
    
    info1 = hasher.get_file_info(file1)
    info2 = hasher.get_file_info(file2)
    
    comparison = {
        'file1': info1,
        'file2': info2,
        'same_hash': info1.get('hash') == info2.get('hash'),
        'same_size': info1.get('size') == info2.get('size'),
        'same_type': info1.get('file_type') == info2.get('file_type'),
        'hash_method1': info1.get('hash_method'),
        'hash_method2': info2.get('hash_method'),
    }
    
    return comparison


def supported_extensions() -> Dict[str, Set[str]]:
    """
    Get all supported file extensions organized by category
    
    Returns:
        Dictionary mapping file types to sets of extensions
    """
    return {
        'images': MediaHasher.IMAGE_EXTENSIONS.copy(),
        'audio': MediaHasher.AUDIO_EXTENSIONS.copy(),
        'video': MediaHasher.VIDEO_EXTENSIONS.copy(),
        'all': (MediaHasher.IMAGE_EXTENSIONS | 
               MediaHasher.AUDIO_EXTENSIONS | 
               MediaHasher.VIDEO_EXTENSIONS)
    }


def filter_files_by_type(file_paths: List[str | Path], 
                        file_types: List[str]) -> List[Path]:
    """
    Filter a list of file paths by file type categories
    
    Args:
        file_paths: List of file paths to filter
        file_types: List of file types to include ('image', 'audio', 'video', 'other')
    
    Returns:
        List of Path objects matching the specified types
    """
    hasher = MediaHasher()  # Just for access to file type detection
    filtered_files = []
    
    for file_path in file_paths:
        path_obj = Path(file_path)
        if path_obj.exists() and path_obj.is_file():
            file_ext = path_obj.suffix.lower()
            file_type = hasher._get_file_type(file_ext)
            
            if file_type in file_types:
                filtered_files.append(path_obj)
    
    return filtered_files


def get_file_stats(file_hashes: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """
    Get statistics about a collection of hashed files
    
    Args:
        file_hashes: Dictionary mapping file paths to hashes
    
    Returns:
        Dictionary with statistics
    """
    total_files = len(file_hashes)
    successful_hashes = sum(1 for h in file_hashes.values() if h is not None)
    failed_hashes = total_files - successful_hashes
    
    # Count unique hashes
    unique_hashes = set(h for h in file_hashes.values() if h is not None)
    
    # Find duplicates
    duplicates = find_duplicates(file_hashes)
    duplicate_groups = len(duplicates)
    total_duplicates = sum(len(files) - 1 for files in duplicates.values())
    
    # File type breakdown
    hasher = MediaHasher()
    type_counts = defaultdict(int)
    for file_path in file_hashes.keys():
        path_obj = Path(file_path)
        file_ext = path_obj.suffix.lower()
        file_type = hasher._get_file_type(file_ext)
        type_counts[file_type] += 1
    
    return {
        'total_files': total_files,
        'successful_hashes': successful_hashes,
        'failed_hashes': failed_hashes,
        'success_rate': successful_hashes / total_files if total_files > 0 else 0,
        'unique_hashes': len(unique_hashes),
        'duplicate_groups': duplicate_groups,
        'total_duplicates': total_duplicates,
        'file_types': dict(type_counts)
    }


def create_hash_report(directory_path: str | Path, 
                      output_file: Optional[str | Path] = None,
                      **kwargs) -> str:
    """
    Create a comprehensive hash report for a directory
    
    Args:
        directory_path: Directory to analyze
        output_file: Optional file to save the report to
        **kwargs: Additional arguments passed to batch_hash_directory
    
    Returns:
        Report as a string
    """
    # Get all file hashes
    file_hashes = batch_hash_directory(directory_path, **kwargs)
    
    # Get statistics
    stats = get_file_stats(file_hashes)
    
    # Find duplicates
    duplicates = find_duplicates(file_hashes)
    
    # Generate report
    report_lines = [
        f"MediaHasher Report for: {directory_path}",
        "=" * 50,
        "",
        "SUMMARY:",
        f"  Total files: {stats['total_files']}",
        f"  Successfully hashed: {stats['successful_hashes']}",
        f"  Failed to hash: {stats['failed_hashes']}",
        f"  Success rate: {stats['success_rate']:.1%}",
        f"  Unique files: {stats['unique_hashes']}",
        f"  Duplicate groups: {stats['duplicate_groups']}",
        f"  Total duplicates: {stats['total_duplicates']}",
        "",
        "FILE TYPES:",
    ]
    
    for file_type, count in stats['file_types'].items():
        report_lines.append(f"  {file_type}: {count}")
    
    if duplicates:
        report_lines.extend([
            "",
            "DUPLICATES:",
        ])
        
        for hash_value, files in duplicates.items():
            report_lines.append(f"  Hash {hash_value}:")
            for file_path in files:
                report_lines.append(f"    - {file_path}")
            report_lines.append("")
    
    report = "\n".join(report_lines)
    
    # Save to file if requested
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report)
    
    return report


class HashCache:
    """
    Simple file hash cache to avoid re-computing hashes for unchanged files
    """
    
    def __init__(self, hasher: Optional[MediaHasher] = None):
        self.hasher = hasher or MediaHasher()
        self.cache = {}  # file_path -> (mtime, hash)
    
    def get_hash(self, file_path: str | Path) -> Optional[str]:
        """
        Get hash for file, using cache if file hasn't changed
        
        Args:
            file_path: Path to file
            
        Returns:
            Hash string or None
        """
        path_obj = Path(file_path)
        
        if not path_obj.exists():
            return None
        
        try:
            current_mtime = path_obj.stat().st_mtime
            file_key = str(path_obj.absolute())
            
            # Check cache
            if file_key in self.cache:
                cached_mtime, cached_hash = self.cache[file_key]
                if cached_mtime == current_mtime:
                    return cached_hash
            
            # Compute new hash
            new_hash = self.hasher.hash_file(path_obj)
            
            # Update cache
            self.cache[file_key] = (current_mtime, new_hash)
            
            return new_hash
            
        except Exception:
            return None
    
    def clear_cache(self):
        """Clear the hash cache"""
        self.cache.clear()
    
    def cache_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            'cached_files': len(self.cache),
            'total_entries': len(self.cache)
        }
