The three CFG/CSV pairs are unmodified ATH V2026-09a captures from mesher
commit 2c82d8297e106e01876ae85b231600506f406f07,
`tests/fixtures/ath_import/ath-v2026-09a/controls`:

- `o-z2-s3`: independent axial scale with a different global scale.
- `r-rot10`: top-level R-OSSE rotation.
- `r-ext-seg4`: authored throat extension interval count.

The grid tolerance is the mesher manifest's 0.0002 mm. These captures are
independent of WG's parser and translator.

`saved-core-v2.json` is the saved CORE design captured with WG
ccbb81ba0303b774b3ea75618dd8d32b9f12ae94. Its serialized CFG SHA-256 is
3ed4df4ec04c7a57961921a720fcf8d7d441b8f482ddfd958640356aba400006,
the existing frozen serializer digest. Keep its saved stamp unchanged.
