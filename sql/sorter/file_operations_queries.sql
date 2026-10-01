-- File Operations Logging Queries
-- Quick reference queries for the file_operations table

-- =====================================================
-- RECENT ACTIVITY
-- =====================================================

-- Last 100 operations
SELECT 
    operation_type,
    file_name,
    source_directory,
    destination_directory,
    reason,
    operation_status,
    performed_at
FROM file_operations
ORDER BY performed_at DESC
LIMIT 100;

-- Operations in last 24 hours
SELECT 
    operation_type,
    COUNT(*) as count,
    SUM(file_size) as total_bytes,
    ROUND(SUM(file_size) / 1024 / 1024, 2) as total_mb
FROM file_operations
WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY operation_type;

-- =====================================================
-- ERROR TRACKING
-- =====================================================

-- Recent failed operations
SELECT 
    operation_type,
    file_name,
    source_path,
    destination_path,
    error_message,
    performed_at
FROM file_operations
WHERE operation_status = 'failed'
ORDER BY performed_at DESC
LIMIT 50;

-- Failed operations summary by error type
SELECT 
    operation_type,
    LEFT(error_message, 100) as error_summary,
    COUNT(*) as count
FROM file_operations
WHERE operation_status = 'failed'
  AND performed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY operation_type, LEFT(error_message, 100)
ORDER BY count DESC;

-- =====================================================
-- FILE HISTORY
-- =====================================================

-- Track specific file movements
SELECT 
    operation_type,
    source_path,
    destination_path,
    source_directory,
    destination_directory,
    reason,
    performed_at
FROM file_operations
WHERE file_name = 'your_file_name_here.jpg'  -- Replace with actual filename
ORDER BY performed_at DESC;

-- Find files moved to a specific directory
SELECT 
    file_name,
    source_directory,
    reason,
    performed_at
FROM file_operations
WHERE destination_directory = 'nsfw_image'  -- Replace with target directory
  AND operation_type = 'move'
  AND performed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
ORDER BY performed_at DESC;

-- =====================================================
-- STATISTICS & ANALYTICS
-- =====================================================

-- Operations by reason (last 30 days)
SELECT 
    reason,
    operation_type,
    COUNT(*) as count,
    ROUND(SUM(file_size) / 1024 / 1024, 2) as total_mb
FROM file_operations
WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
  AND operation_status = 'success'
GROUP BY reason, operation_type
ORDER BY count DESC;

-- Daily operation counts (last 7 days)
SELECT 
    DATE(performed_at) as date,
    operation_type,
    COUNT(*) as count
FROM file_operations
WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY DATE(performed_at), operation_type
ORDER BY date DESC, operation_type;

-- Hourly distribution (last 24 hours)
SELECT 
    HOUR(performed_at) as hour,
    operation_type,
    COUNT(*) as count
FROM file_operations
WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY HOUR(performed_at), operation_type
ORDER BY hour, operation_type;

-- =====================================================
-- DUPLICATES ANALYSIS
-- =====================================================

-- Duplicate-related operations
SELECT 
    reason,
    COUNT(*) as count,
    ROUND(SUM(file_size) / 1024 / 1024, 2) as total_mb
FROM file_operations
WHERE reason LIKE '%duplicate%'
  AND performed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY reason
ORDER BY count DESC;

-- Files moved to duplicates folder
SELECT 
    file_name,
    source_directory,
    reason,
    ROUND(file_size / 1024 / 1024, 2) as size_mb,
    performed_at
FROM file_operations
WHERE destination_directory = 'duplicates'
  AND operation_type = 'move'
ORDER BY performed_at DESC
LIMIT 100;

-- =====================================================
-- ARCHIVE OPERATIONS
-- =====================================================

-- Recent archive operations
SELECT 
    file_name,
    source_directory,
    destination_directory,
    reason,
    ROUND(file_size / 1024 / 1024, 2) as size_mb,
    performed_at
FROM file_operations
WHERE reason LIKE 'archive_%'
ORDER BY performed_at DESC
LIMIT 100;

-- Archive statistics by source
SELECT 
    source_directory,
    COUNT(*) as files_archived,
    ROUND(SUM(file_size) / 1024 / 1024 / 1024, 2) as total_gb
FROM file_operations
WHERE reason LIKE 'archive_%'
  AND performed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY source_directory
ORDER BY files_archived DESC;

-- =====================================================
-- ARTIST OPERATIONS
-- =====================================================

-- Artist-based movements
SELECT 
    reason,
    COUNT(*) as count,
    ROUND(SUM(file_size) / 1024 / 1024, 2) as total_mb
FROM file_operations
WHERE reason LIKE 'artist_%' OR reason LIKE 'favorite_artist_%'
  AND performed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY reason
ORDER BY count DESC;

-- Files moved to artist directories
SELECT 
    file_name,
    source_directory,
    reason,
    performed_at
FROM file_operations
WHERE destination_directory = 'artist_directory'
  AND operation_type = 'move'
ORDER BY performed_at DESC
LIMIT 100;

-- =====================================================
-- SIZE ANALYSIS
-- =====================================================

-- Operations by file size range
SELECT 
    CASE 
        WHEN file_size < 1024*1024 THEN '< 1 MB'
        WHEN file_size < 10*1024*1024 THEN '1-10 MB'
        WHEN file_size < 50*1024*1024 THEN '10-50 MB'
        WHEN file_size < 100*1024*1024 THEN '50-100 MB'
        ELSE '> 100 MB'
    END as size_range,
    operation_type,
    COUNT(*) as count
FROM file_operations
WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
  AND file_size IS NOT NULL
GROUP BY size_range, operation_type
ORDER BY 
    CASE 
        WHEN file_size < 1024*1024 THEN 1
        WHEN file_size < 10*1024*1024 THEN 2
        WHEN file_size < 50*1024*1024 THEN 3
        WHEN file_size < 100*1024*1024 THEN 4
        ELSE 5
    END;

-- Largest files moved
SELECT 
    file_name,
    ROUND(file_size / 1024 / 1024, 2) as size_mb,
    source_directory,
    destination_directory,
    reason,
    performed_at
FROM file_operations
WHERE operation_type = 'move'
  AND performed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
ORDER BY file_size DESC
LIMIT 50;

-- =====================================================
-- CLEANUP & MAINTENANCE
-- =====================================================

-- Delete operations older than 90 days
-- CAUTION: Run selectively, not automatically!
-- DELETE FROM file_operations 
-- WHERE performed_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- Count old operations
SELECT 
    COUNT(*) as old_operations,
    MIN(performed_at) as oldest,
    MAX(performed_at) as newest
FROM file_operations
WHERE performed_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- Table size and row count
SELECT 
    COUNT(*) as total_operations,
    MIN(performed_at) as first_operation,
    MAX(performed_at) as last_operation,
    ROUND(SUM(file_size) / 1024 / 1024 / 1024, 2) as total_data_moved_gb
FROM file_operations;
