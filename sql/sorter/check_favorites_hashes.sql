-- Check if favorites table has hash values
USE image_sorting_hashes;

-- Count total files
SELECT COUNT(*) as total_files FROM favorites;

-- Count files with at least one perceptual hash
SELECT COUNT(*) as files_with_hash
FROM favorites
WHERE perceptual_hash_dhash IS NOT NULL
   OR perceptual_hash_phash IS NOT NULL
   OR perceptual_hash_average IS NOT NULL
   OR perceptual_hash_whash IS NOT NULL
   OR md5_hash IS NOT NULL;

-- Sample of first 10 files and their hash status
SELECT
    file_name,
    perceptual_hash_dhash IS NOT NULL as has_dhash,
    perceptual_hash_phash IS NOT NULL as has_phash,
    perceptual_hash_average IS NOT NULL as has_avg,
    perceptual_hash_whash IS NOT NULL as has_whash,
    md5_hash IS NOT NULL as has_md5
FROM favorites
LIMIT 10;
