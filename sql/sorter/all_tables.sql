-- image_sorting_hashes.all_files source

CREATE OR REPLACE
ALGORITHM = UNDEFINED VIEW `image_sorting_hashes`.`all_files` AS
select
    `f`.`file_name` AS `file_name`,
    `f`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `f`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `f`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `f`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `f`.`file_size` AS `file_size`,
    `f`.`file_modified_at` AS `file_modified_at`,
    `f`.`file_path` AS `file_path`,
    `f`.`md5_hash` AS `md5_hash`,
    `f`.`e621_post_id` AS `e621_post_id`,
    `f`.`hash_type` AS `hash_type`,
    `f`.`file_created_at` AS `file_created_at`,
    `f`.`last_artist_check` AS `last_artist_check`,
    `f`.`artist_matched` AS `artist_matched`,
    'favorites' AS `table_name`
from
    `image_sorting_hashes`.`favorites` `f`
union all
select
    `mi`.`file_name` AS `file_name`,
    `mi`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `mi`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `mi`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `mi`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `mi`.`file_size` AS `file_size`,
    `mi`.`file_modified_at` AS `file_modified_at`,
    `mi`.`file_path` AS `file_path`,
    `mi`.`md5_hash` AS `md5_hash`,
    `mi`.`e621_post_id` AS `e621_post_id`,
    `mi`.`hash_type` AS `hash_type`,
    `mi`.`file_created_at` AS `file_created_at`,
    `mi`.`last_artist_check` AS `last_artist_check`,
    `mi`.`artist_matched` AS `artist_matched`,
    'manual_sort_images' AS `table_name`
from
    `image_sorting_hashes`.`manual_sort_images` `mi`
union all
select
    `mv`.`file_name` AS `file_name`,
    `mv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `mv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `mv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `mv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `mv`.`file_size` AS `file_size`,
    `mv`.`file_modified_at` AS `file_modified_at`,
    `mv`.`file_path` AS `file_path`,
    `mv`.`md5_hash` AS `md5_hash`,
    `mv`.`e621_post_id` AS `e621_post_id`,
    `mv`.`hash_type` AS `hash_type`,
    `mv`.`file_created_at` AS `file_created_at`,
    `mv`.`last_artist_check` AS `last_artist_check`,
    `mv`.`artist_matched` AS `artist_matched`,
    'manual_sort_videos' AS `table_name`
from
    `image_sorting_hashes`.`manual_sort_videos` `mv`
union all
select
    `si`.`file_name` AS `file_name`,
    `si`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `si`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `si`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `si`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `si`.`file_size` AS `file_size`,
    `si`.`file_modified_at` AS `file_modified_at`,
    `si`.`file_path` AS `file_path`,
    `si`.`md5_hash` AS `md5_hash`,
    `si`.`e621_post_id` AS `e621_post_id`,
    `si`.`hash_type` AS `hash_type`,
    `si`.`file_created_at` AS `file_created_at`,
    `si`.`last_artist_check` AS `last_artist_check`,
    `si`.`artist_matched` AS `artist_matched`,
    'sfw_image' AS `table_name`
from
    `image_sorting_hashes`.`sfw_image` `si`
union all
select
    `sv`.`file_name` AS `file_name`,
    `sv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `sv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `sv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `sv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `sv`.`file_size` AS `file_size`,
    `sv`.`file_modified_at` AS `file_modified_at`,
    `sv`.`file_path` AS `file_path`,
    `sv`.`md5_hash` AS `md5_hash`,
    `sv`.`e621_post_id` AS `e621_post_id`,
    `sv`.`hash_type` AS `hash_type`,
    `sv`.`file_created_at` AS `file_created_at`,
    `sv`.`last_artist_check` AS `last_artist_check`,
    `sv`.`artist_matched` AS `artist_matched`,
    'sfw_video' AS `table_name`
from
    `image_sorting_hashes`.`sfw_video` `sv`
union all
select
    `sg`.`file_name` AS `file_name`,
    `sg`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `sg`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `sg`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `sg`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `sg`.`file_size` AS `file_size`,
    `sg`.`file_modified_at` AS `file_modified_at`,
    `sg`.`file_path` AS `file_path`,
    `sg`.`md5_hash` AS `md5_hash`,
    `sg`.`e621_post_id` AS `e621_post_id`,
    `sg`.`hash_type` AS `hash_type`,
    `sg`.`file_created_at` AS `file_created_at`,
    `sg`.`last_artist_check` AS `last_artist_check`,
    `sg`.`artist_matched` AS `artist_matched`,
    'sfw_gif' AS `table_name`
from
    `image_sorting_hashes`.`sfw_gif` `sg`
union all
select
    `ni`.`file_name` AS `file_name`,
    `ni`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `ni`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `ni`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `ni`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `ni`.`file_size` AS `file_size`,
    `ni`.`file_modified_at` AS `file_modified_at`,
    `ni`.`file_path` AS `file_path`,
    `ni`.`md5_hash` AS `md5_hash`,
    `ni`.`e621_post_id` AS `e621_post_id`,
    `ni`.`hash_type` AS `hash_type`,
    `ni`.`file_created_at` AS `file_created_at`,
    `ni`.`last_artist_check` AS `last_artist_check`,
    `ni`.`artist_matched` AS `artist_matched`,
    'nsfw_image' AS `table_name`
from
    `image_sorting_hashes`.`nsfw_image` `ni`
union all
select
    `nv`.`file_name` AS `file_name`,
    `nv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `nv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `nv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `nv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `nv`.`file_size` AS `file_size`,
    `nv`.`file_modified_at` AS `file_modified_at`,
    `nv`.`file_path` AS `file_path`,
    `nv`.`md5_hash` AS `md5_hash`,
    `nv`.`e621_post_id` AS `e621_post_id`,
    `nv`.`hash_type` AS `hash_type`,
    `nv`.`file_created_at` AS `file_created_at`,
    `nv`.`last_artist_check` AS `last_artist_check`,
    `nv`.`artist_matched` AS `artist_matched`,
    'nsfw_video' AS `table_name`
from
    `image_sorting_hashes`.`nsfw_video` `nv`
union all
select
    `ng`.`file_name` AS `file_name`,
    `ng`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `ng`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `ng`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `ng`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `ng`.`file_size` AS `file_size`,
    `ng`.`file_modified_at` AS `file_modified_at`,
    `ng`.`file_path` AS `file_path`,
    `ng`.`md5_hash` AS `md5_hash`,
    `ng`.`e621_post_id` AS `e621_post_id`,
    `ng`.`hash_type` AS `hash_type`,
    `ng`.`file_created_at` AS `file_created_at`,
    `ng`.`last_artist_check` AS `last_artist_check`,
    `ng`.`artist_matched` AS `artist_matched`,
    'nsfw_gif' AS `table_name`
from
    `image_sorting_hashes`.`nsfw_gif` `ng`
union all
select
    `aeg`.`file_name` AS `file_name`,
    `aeg`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `aeg`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `aeg`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `aeg`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `aeg`.`file_size` AS `file_size`,
    `aeg`.`file_modified_at` AS `file_modified_at`,
    `aeg`.`file_path` AS `file_path`,
    `aeg`.`md5_hash` AS `md5_hash`,
    `aeg`.`e621_post_id` AS `e621_post_id`,
    `aeg`.`hash_type` AS `hash_type`,
    `aeg`.`file_created_at` AS `file_created_at`,
    `aeg`.`last_artist_check` AS `last_artist_check`,
    `aeg`.`artist_matched` AS `artist_matched`,
    'archive_explicit_gifs' AS `table_name`
from
    `image_sorting_hashes`.`archive_explicit_gifs` `aeg`
union all
select
    `aei`.`file_name` AS `file_name`,
    `aei`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `aei`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `aei`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `aei`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `aei`.`file_size` AS `file_size`,
    `aei`.`file_modified_at` AS `file_modified_at`,
    `aei`.`file_path` AS `file_path`,
    `aei`.`md5_hash` AS `md5_hash`,
    `aei`.`e621_post_id` AS `e621_post_id`,
    `aei`.`hash_type` AS `hash_type`,
    `aei`.`file_created_at` AS `file_created_at`,
    `aei`.`last_artist_check` AS `last_artist_check`,
    `aei`.`artist_matched` AS `artist_matched`,
    'archive_explicit_images' AS `table_name`
from
    `image_sorting_hashes`.`archive_explicit_images` `aei`
union all
select
    `aev`.`file_name` AS `file_name`,
    `aev`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `aev`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `aev`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `aev`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `aev`.`file_size` AS `file_size`,
    `aev`.`file_modified_at` AS `file_modified_at`,
    `aev`.`file_path` AS `file_path`,
    `aev`.`md5_hash` AS `md5_hash`,
    `aev`.`e621_post_id` AS `e621_post_id`,
    `aev`.`hash_type` AS `hash_type`,
    `aev`.`file_created_at` AS `file_created_at`,
    `aev`.`last_artist_check` AS `last_artist_check`,
    `aev`.`artist_matched` AS `artist_matched`,
    'archive_explicit_videos' AS `table_name`
from
    `image_sorting_hashes`.`archive_explicit_videos` `aev`
union all
select
    `ap`.`file_name` AS `file_name`,
    `ap`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `ap`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `ap`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `ap`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `ap`.`file_size` AS `file_size`,
    `ap`.`file_modified_at` AS `file_modified_at`,
    `ap`.`file_path` AS `file_path`,
    `ap`.`md5_hash` AS `md5_hash`,
    `ap`.`e621_post_id` AS `e621_post_id`,
    `ap`.`hash_type` AS `hash_type`,
    `ap`.`file_created_at` AS `file_created_at`,
    `ap`.`last_artist_check` AS `last_artist_check`,
    `ap`.`artist_matched` AS `artist_matched`,
    'archive_premium' AS `table_name`
from
    `image_sorting_hashes`.`archive_premium` `ap`
union all
select
    `asg`.`file_name` AS `file_name`,
    `asg`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `asg`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `asg`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `asg`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `asg`.`file_size` AS `file_size`,
    `asg`.`file_modified_at` AS `file_modified_at`,
    `asg`.`file_path` AS `file_path`,
    `asg`.`md5_hash` AS `md5_hash`,
    `asg`.`e621_post_id` AS `e621_post_id`,
    `asg`.`hash_type` AS `hash_type`,
    `asg`.`file_created_at` AS `file_created_at`,
    `asg`.`last_artist_check` AS `last_artist_check`,
    `asg`.`artist_matched` AS `artist_matched`,
    'archive_sfw_gifs' AS `table_name`
from
    `image_sorting_hashes`.`archive_sfw_gifs` `asg`
union all
select
    `asi`.`file_name` AS `file_name`,
    `asi`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `asi`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `asi`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `asi`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `asi`.`file_size` AS `file_size`,
    `asi`.`file_modified_at` AS `file_modified_at`,
    `asi`.`file_path` AS `file_path`,
    `asi`.`md5_hash` AS `md5_hash`,
    `asi`.`e621_post_id` AS `e621_post_id`,
    `asi`.`hash_type` AS `hash_type`,
    `asi`.`file_created_at` AS `file_created_at`,
    `asi`.`last_artist_check` AS `last_artist_check`,
    `asi`.`artist_matched` AS `artist_matched`,
    'archive_sfw_images' AS `table_name`
from
    `image_sorting_hashes`.`archive_sfw_images` `asi`
union all
select
    `asv`.`file_name` AS `file_name`,
    `asv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `asv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `asv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `asv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `asv`.`file_size` AS `file_size`,
    `asv`.`file_modified_at` AS `file_modified_at`,
    `asv`.`file_path` AS `file_path`,
    `asv`.`md5_hash` AS `md5_hash`,
    `asv`.`e621_post_id` AS `e621_post_id`,
    `asv`.`hash_type` AS `hash_type`,
    `asv`.`file_created_at` AS `file_created_at`,
    `asv`.`last_artist_check` AS `last_artist_check`,
    `asv`.`artist_matched` AS `artist_matched`,
    'archive_sfw_videos' AS `table_name`
from
    `image_sorting_hashes`.`archive_sfw_videos` `asv`
union all
select
    `af`.`file_name` AS `file_name`,
    `af`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `af`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `af`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `af`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `af`.`file_size` AS `file_size`,
    `af`.`file_modified_at` AS `file_modified_at`,
    `af`.`file_path` AS `file_path`,
    `af`.`md5_hash` AS `md5_hash`,
    `af`.`e621_post_id` AS `e621_post_id`,
    `af`.`hash_type` AS `hash_type`,
    `af`.`file_created_at` AS `file_created_at`,
    `af`.`last_artist_check` AS `last_artist_check`,
    `af`.`artist_matched` AS `artist_matched`,
    'archived_favorites' AS `table_name`
from
    `image_sorting_hashes`.`archived_favorites` `af`
union all
select
    `oa`.`file_name` AS `file_name`,
    `oa`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `oa`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `oa`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `oa`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `oa`.`file_size` AS `file_size`,
    `oa`.`file_modified_at` AS `file_modified_at`,
    `oa`.`file_path` AS `file_path`,
    `oa`.`md5_hash` AS `md5_hash`,
    `oa`.`e621_post_id` AS `e621_post_id`,
    `oa`.`hash_type` AS `hash_type`,
    `oa`.`file_created_at` AS `file_created_at`,
    `oa`.`last_artist_check` AS `last_artist_check`,
    `oa`.`artist_matched` AS `artist_matched`,
    'other_directories' AS `table_name`
from
    `image_sorting_hashes`.`other_directories` `oa`;