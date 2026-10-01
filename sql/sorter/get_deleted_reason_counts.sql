SELECT
    DATE(p.deleted_at) AS deletion_date,
    CASE
        -- Artist-related removals
        WHEN p.deletion_reason LIKE '%Artist requested removal%'
            OR p.deletion_reason LIKE '%artist is on the avoid posting list%'
            THEN 'Artist requested removal'

        -- Character owner removals
        WHEN p.deletion_reason LIKE '%Character Owner requested removal%'
            THEN 'Character Owner requested removal'

        -- Underage artist
        WHEN p.deletion_reason LIKE '%nderage artist%'
            THEN 'Underage artist'

        -- Paysite/commercial content
        WHEN p.deletion_reason LIKE '%Paysite%'
            OR p.deletion_reason LIKE '%commercial content%'
            OR p.deletion_reason LIKE '%Patreon%'
            OR p.deletion_reason LIKE '%Subscribestar%'
            THEN 'Paysite/Commercial content'

        -- Quality issues
        WHEN p.deletion_reason LIKE '%Does not meet minimum quality standards%'
            OR p.deletion_reason LIKE '%low quality%'
            THEN 'Does not meet quality standards'

        -- Conditional DNP
        WHEN p.deletion_reason LIKE '%Conditional DNP%'
            THEN 'Conditional DNP'

        -- Real-life pornography
        WHEN p.deletion_reason LIKE '%eal-life%pornography%'
            OR p.deletion_reason LIKE '%Real-life pornography%'
            THEN 'Real-life pornography'

        -- Screencap
        WHEN p.deletion_reason LIKE '%Screencap%'
            THEN 'Screencap'

        -- Inferior/duplicate
        WHEN p.deletion_reason LIKE '%Inferior%'
            OR p.deletion_reason LIKE '%duplicate%'
            THEN 'Inferior/Duplicate'

        -- Unapproved
        WHEN p.deletion_reason LIKE '%Unapproved%'
            THEN 'Unapproved in 30 days'

        -- Irrelevant
        WHEN p.deletion_reason LIKE '%Irrelevant%'
            THEN 'Irrelevant to site'

        -- Editor requested
        WHEN p.deletion_reason LIKE '%Editor requested%'
            THEN 'Editor requested removal'

        -- Everything else
        ELSE p.deletion_reason
    END AS deletion_reason_grouped,
    COUNT(p.id) AS post_count
FROM posts p
WHERE p.deletion_reason IS NOT NULL
AND p.deletion_reason != ''
AND p.deleted_at IS NOT NULL
GROUP BY deletion_date, deletion_reason_grouped
ORDER BY deletion_date DESC, post_count DESC