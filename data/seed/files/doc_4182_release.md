# Summit Search 4.18.2 release notes

- Document ID: `doc_4182_release`
- Type: `release-notes`
- Team: `product`
- Version: `4.18.2`
- Updated: `2026-07-10T12:00:00Z`

## Index maintenance

<a id="pas_4182_compaction"></a>
Version 4.18.2 reduces average compaction duration for newly created indexes. Existing large shards can temporarily use more memory during their first post-upgrade compaction. Schedule that operation outside peak query periods.

## Known limitations

<a id="pas_4182_known"></a>
On clusters with shards above 1.5 TB, compaction concurrency must remain at one until the shard has completed its first 4.18.2 cycle.
