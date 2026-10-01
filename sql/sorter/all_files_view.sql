-- image_sorting_hashes.all_files source

CREATE OR REPLACE
ALGORITHM = UNDEFINED VIEW `image_sorting_hashes`.`all_files` AS
select
    `image_sorting_hashes`.`f`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`f`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`f`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`f`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`f`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`f`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`f`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`f`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`f`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`f`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`f`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`f`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`f`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`f`.`artist_matched` AS `artist_matched`,
    'favorites' AS `table_name`
from
    `image_sorting_hashes`.`favorites` `f`
union all
select
    `image_sorting_hashes`.`mi`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`mi`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`mi`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`mi`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`mi`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`mi`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`mi`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`mi`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`mi`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`mi`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`mi`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`mi`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`mi`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`mi`.`artist_matched` AS `artist_matched`,
    'manual_sort_images' AS `table_name`
from
    `image_sorting_hashes`.`manual_sort_images` `mi`
union all
select
    `image_sorting_hashes`.`mv`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`mv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`mv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`mv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`mv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`mv`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`mv`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`mv`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`mv`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`mv`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`mv`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`mv`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`mv`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`mv`.`artist_matched` AS `artist_matched`,
    'manual_sort_videos' AS `table_name`
from
    `image_sorting_hashes`.`manual_sort_videos` `mv`
union all
select
    `image_sorting_hashes`.`si`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`si`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`si`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`si`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`si`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`si`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`si`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`si`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`si`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`si`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`si`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`si`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`si`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`si`.`artist_matched` AS `artist_matched`,
    'sfw_image' AS `table_name`
from
    `image_sorting_hashes`.`sfw_image` `si`
union all
select
    `image_sorting_hashes`.`sv`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`sv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`sv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`sv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`sv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`sv`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`sv`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`sv`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`sv`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`sv`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`sv`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`sv`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`sv`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`sv`.`artist_matched` AS `artist_matched`,
    'sfw_video' AS `table_name`
from
    `image_sorting_hashes`.`sfw_video` `sv`
union all
select
    `image_sorting_hashes`.`sg`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`sg`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`sg`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`sg`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`sg`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`sg`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`sg`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`sg`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`sg`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`sg`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`sg`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`sg`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`sg`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`sg`.`artist_matched` AS `artist_matched`,
    'sfw_gif' AS `table_name`
from
    `image_sorting_hashes`.`sfw_gif` `sg`
union all
select
    `image_sorting_hashes`.`ni`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`ni`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`ni`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`ni`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`ni`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`ni`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`ni`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`ni`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`ni`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`ni`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`ni`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`ni`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`ni`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`ni`.`artist_matched` AS `artist_matched`,
    'nsfw_image' AS `table_name`
from
    `image_sorting_hashes`.`nsfw_image` `ni`
union all
select
    `image_sorting_hashes`.`nv`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`nv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`nv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`nv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`nv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`nv`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`nv`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`nv`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`nv`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`nv`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`nv`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`nv`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`nv`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`nv`.`artist_matched` AS `artist_matched`,
    'nsfw_video' AS `table_name`
from
    `image_sorting_hashes`.`nsfw_video` `nv`
union all
select
    `image_sorting_hashes`.`ng`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`ng`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`ng`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`ng`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`ng`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`ng`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`ng`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`ng`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`ng`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`ng`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`ng`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`ng`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`ng`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`ng`.`artist_matched` AS `artist_matched`,
    'nsfw_gif' AS `table_name`
from
    `image_sorting_hashes`.`nsfw_gif` `ng`
union all
select
    `image_sorting_hashes`.`aei`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`aei`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`aei`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`aei`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`aei`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`aei`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`aei`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`aei`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`aei`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`aei`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`aei`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`aei`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`aei`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`aei`.`artist_matched` AS `artist_matched`,
    'archive_explicit_images' AS `table_name`
from
    `image_sorting_hashes`.`archive_explicit_images` `aei`
union all
select
    `image_sorting_hashes`.`aev`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`aev`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`aev`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`aev`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`aev`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`aev`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`aev`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`aev`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`aev`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`aev`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`aev`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`aev`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`aev`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`aev`.`artist_matched` AS `artist_matched`,
    'archive_explicit_videos' AS `table_name`
from
    `image_sorting_hashes`.`archive_explicit_videos` `aev`
union all
select
    `image_sorting_hashes`.`aeg`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`aeg`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`aeg`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`aeg`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`aeg`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`aeg`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`aeg`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`aeg`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`aeg`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`aeg`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`aeg`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`aeg`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`aeg`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`aeg`.`artist_matched` AS `artist_matched`,
    'archive_explicit_gifs' AS `table_name`
from
    `image_sorting_hashes`.`archive_explicit_gifs` `aeg`
union all
select
    `image_sorting_hashes`.`asi`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`asi`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`asi`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`asi`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`asi`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`asi`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`asi`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`asi`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`asi`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`asi`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`asi`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`asi`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`asi`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`asi`.`artist_matched` AS `artist_matched`,
    'archive_sfw_images' AS `table_name`
from
    `image_sorting_hashes`.`archive_sfw_images` `asi`
union all
select
    `image_sorting_hashes`.`asv`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`asv`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`asv`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`asv`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`asv`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`asv`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`asv`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`asv`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`asv`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`asv`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`asv`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`asv`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`asv`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`asv`.`artist_matched` AS `artist_matched`,
    'archive_sfw_videos' AS `table_name`
from
    `image_sorting_hashes`.`archive_sfw_videos` `asv`
union all
select
    `image_sorting_hashes`.`asg`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`asg`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`asg`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`asg`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`asg`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`asg`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`asg`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`asg`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`asg`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`asg`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`asg`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`asg`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`asg`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`asg`.`artist_matched` AS `artist_matched`,
    'archive_sfw_gifs' AS `table_name`
from
    `image_sorting_hashes`.`archive_sfw_gifs` `asg`
union all
select
    `image_sorting_hashes`.`af`.`file_name` AS `file_name`,
    `image_sorting_hashes`.`af`.`perceptual_hash_dhash` AS `perceptual_hash_dhash`,
    `image_sorting_hashes`.`af`.`perceptual_hash_phash` AS `perceptual_hash_phash`,
    `image_sorting_hashes`.`af`.`perceptual_hash_average` AS `perceptual_hash_average`,
    `image_sorting_hashes`.`af`.`perceptual_hash_whash` AS `perceptual_hash_whash`,
    `image_sorting_hashes`.`af`.`file_size` AS `file_size`,
    `image_sorting_hashes`.`af`.`file_modified_at` AS `file_modified_at`,
    `image_sorting_hashes`.`af`.`file_path` AS `file_path`,
    `image_sorting_hashes`.`af`.`md5_hash` AS `md5_hash`,
    `image_sorting_hashes`.`af`.`e621_post_id` AS `e621_post_id`,
    `image_sorting_hashes`.`af`.`hash_type` AS `hash_type`,
    `image_sorting_hashes`.`af`.`file_created_at` AS `file_created_at`,
    `image_sorting_hashes`.`af`.`last_artist_check` AS `last_artist_check`,
    `image_sorting_hashes`.`af`.`artist_matched` AS `artist_matched`,
    'archived_favorites' AS `table_name`
from
    `image_sorting_hashes`.`archived_favorites` `af`;