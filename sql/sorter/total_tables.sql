-- image_sorting_hashes.table_totals source

CREATE OR REPLACE
ALGORITHM = UNDEFINED VIEW `image_sorting_hashes`.`table_totals` AS
select
    'Favorites' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`favorites`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`favorites`
union all
select
    'Need To Be Sorted Pictures' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`manual_sort_images`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`manual_sort_images`
union all
select
    'Need To Be Sorted Videos' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`manual_sort_videos`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`manual_sort_videos`
union all
select
    'Need To Be Sorted Gifs' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`manual_sort_gifs`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`manual_sort_gifs`
union all
select
    'SFW Images' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`sfw_image`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`sfw_image`
union all
select
    'SFW Videos' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`sfw_video`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`sfw_video`
union all
select
    'SFW Gifs' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`sfw_gif`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`sfw_gif`
union all
select
    'NSFW Images' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`nsfw_image`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`nsfw_image`
union all
select
    'NSFW Videos' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`nsfw_video`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`nsfw_video`
union all
select
    'NSFW Gifs' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`nsfw_gif`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`nsfw_gif`
union all
select
    'Other Directories' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`other_directories`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`other_directories`
union all
select
    'Archive Premium' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_premium`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_premium`
union all
select
    'Archive NSFW Images' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_explicit_images`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_explicit_images`
union all
select
    'Archive NSFW Videos' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_explicit_videos`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_explicit_videos`
union all
select
    'Archive NSFW Gifs' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_explicit_gifs`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_explicit_gifs`
union all
select
    'Archive SFW Images' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_sfw_images`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_sfw_images`
union all
select
    'Archive SFW Videos' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_sfw_videos`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_sfw_videos`
union all
select
    'Archive SFW Gifs' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archive_sfw_gifs`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archive_sfw_gifs`
union all
select
    'Archived Favorites' AS `table_name`,
    count(0) AS `total_rows`,
    max(`image_sorting_hashes`.`archived_favorites`.`file_modified_at`) AS `latest_file_modified_at`
from
    `image_sorting_hashes`.`archived_favorites`;