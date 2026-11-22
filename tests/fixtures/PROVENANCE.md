# Binary fixtures: where each one came from

Every binary file the public tree carries is listed below, path by path, under the paragraph that
says how it was made. `tools/export_public.py` refuses to export a binary file this list does not
name. **None of them is, or was cut from, a real photograph:** each is synthesised from
hand-written bytes or drawn by a program, so none carries a real person, place, camera serial or
GPS position.

Adding a binary fixture means adding its path here, in the same commit, under the paragraph that
describes how it was made (or a new paragraph if none does).

## The tiny library (`tests/tiny_library/`)

Synthesised by `tools/build_tiny_library.py` from hand-written bytes: minimal JPEG, PNG, TIFF, HEIC
and RAW containers and minimal QuickTime/MP4 boxes, with EXIF and QuickTime dates, GPS positions
and camera names typed into the builder. The images are a few flat or patterned pixels. The
committed files are checked byte for byte against `tests/tiny_library_manifest.json`, and the
builder's own output is compared with them.


- `tests/tiny_library/2005-06-07_holiday.jpg`
- `tests/tiny_library/20060708_trip.JPG`
- `tests/tiny_library/2008.09.10_scan.tif`
- `tests/tiny_library/2013 Vacation/sub_dateless.jpg`
- `tests/tiny_library/2013 Vacation/sub_exif_a.jpg`
- `tests/tiny_library/2013 Vacation/sub_exif_b.JPG`
- `tests/tiny_library/2013 Vacation/sub_pair.JPG`
- `tests/tiny_library/2013 Vacation/sub_pair.NEF`
- `tests/tiny_library/2013 Vacation/sub_video.MOV`
- `tests/tiny_library/2018-05-04_named_clip.mp4`
- `tests/tiny_library/2022-02-02_extcase_arw_lower.arw`
- `tests/tiny_library/2022-02-02_extcase_arw_upper.ARW`
- `tests/tiny_library/2022-02-02_extcase_avi_lower.avi`
- `tests/tiny_library/2022-02-02_extcase_avi_upper.AVI`
- `tests/tiny_library/2022-02-02_extcase_cr2_lower.cr2`
- `tests/tiny_library/2022-02-02_extcase_cr2_upper.CR2`
- `tests/tiny_library/2022-02-02_extcase_dng_lower.dng`
- `tests/tiny_library/2022-02-02_extcase_dng_upper.DNG`
- `tests/tiny_library/2022-02-02_extcase_heic_lower.heic`
- `tests/tiny_library/2022-02-02_extcase_heic_upper.HEIC`
- `tests/tiny_library/2022-02-02_extcase_heif_lower.heif`
- `tests/tiny_library/2022-02-02_extcase_heif_upper.HEIF`
- `tests/tiny_library/2022-02-02_extcase_jpeg_lower.jpeg`
- `tests/tiny_library/2022-02-02_extcase_jpeg_upper.JPEG`
- `tests/tiny_library/2022-02-02_extcase_jpg_lower.jpg`
- `tests/tiny_library/2022-02-02_extcase_jpg_upper.JPG`
- `tests/tiny_library/2022-02-02_extcase_mov_lower.mov`
- `tests/tiny_library/2022-02-02_extcase_mov_upper.MOV`
- `tests/tiny_library/2022-02-02_extcase_mp4_lower.mp4`
- `tests/tiny_library/2022-02-02_extcase_mp4_upper.MP4`
- `tests/tiny_library/2022-02-02_extcase_mts_lower.mts`
- `tests/tiny_library/2022-02-02_extcase_mts_upper.MTS`
- `tests/tiny_library/2022-02-02_extcase_nef_lower.nef`
- `tests/tiny_library/2022-02-02_extcase_nef_upper.NEF`
- `tests/tiny_library/2022-02-02_extcase_png_lower.png`
- `tests/tiny_library/2022-02-02_extcase_png_upper.PNG`
- `tests/tiny_library/2022-02-02_extcase_tif_lower.tif`
- `tests/tiny_library/2022-02-02_extcase_tif_upper.TIF`
- `tests/tiny_library/2022-02-02_extcase_tiff_lower.tiff`
- `tests/tiny_library/2022-02-02_extcase_tiff_upper.TIFF`
- `tests/tiny_library/DSC_1001.JPG`
- `tests/tiny_library/DSC_1001.NEF`
- `tests/tiny_library/IMG_2002.CR2`
- `tests/tiny_library/IMG_2002.JPG`
- `tests/tiny_library/IMG_20070809_120000.jpg`
- `tests/tiny_library/IMG_3001.HEIC`
- `tests/tiny_library/IMG_3001.MOV`
- `tests/tiny_library/IMG_3002.JPG`
- `tests/tiny_library/IMG_3002.mov`
- `tests/tiny_library/café_señor.jpg`
- `tests/tiny_library/clip_avchd.MTS`
- `tests/tiny_library/clip_camcorder.AVI`
- `tests/tiny_library/com0.JPG`
- `tests/tiny_library/corrupt_broken.jpg`
- `tests/tiny_library/edited_photo.JPG`
- `tests/tiny_library/exif_basic.jpg`
- `tests/tiny_library/exif_gps.JPG`
- `tests/tiny_library/exif_gps_south.jpg`
- `tests/tiny_library/exif_offset.jpeg`
- `tests/tiny_library/exif_plain.tif`
- `tests/tiny_library/exif_png.png`
- `tests/tiny_library/exif_upper.TIFF`
- `tests/tiny_library/left_behind_pair.JPG`
- `tests/tiny_library/long_path_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.JPG`
- `tests/tiny_library/no_date_alpha.jpg`
- `tests/tiny_library/no_date_beta.PNG`
- `tests/tiny_library/no_date_delta.HEIC`
- `tests/tiny_library/no_date_epsilon.HEIF`
- `tests/tiny_library/no_date_gamma.tif`
- `tests/tiny_library/party_🎉_time.jpg`
- `tests/tiny_library/phash_pattern_base.png`
- `tests/tiny_library/phash_pattern_far.png`
- `tests/tiny_library/phash_pattern_near.png`
- `tests/tiny_library/raw_adobe.DNG`
- `tests/tiny_library/raw_canon.CR2`
- `tests/tiny_library/raw_nikon.NEF`
- `tests/tiny_library/raw_preview.ARW`
- `tests/tiny_library/raw_sony.ARW`
- `tests/tiny_library/scan_ocr_placeholder_01.jpg`
- `tests/tiny_library/scan_ocr_placeholder_02.tif`
- `tests/tiny_library/sidecar_photo.JPG`
- `tests/tiny_library/takeout_photo.JPG`
- `tests/tiny_library/video_epoch_sentinel.mp4`
- `tests/tiny_library/video_moov_last.MP4`
- `tests/tiny_library/video_no_moov.MOV`
- `tests/tiny_library/video_qt_local.MOV`
- `tests/tiny_library/video_qt_utc_gps.mp4`
- `tests/tiny_library/video_zero_offset.MOV`
- `tests/tiny_library/wrong_clock_1980.jpg`
- `tests/tiny_library/wrong_clock_future.jpg`
- `tests/tiny_library/Ελληνικά.jpg`
- `tests/tiny_library/Иванов_семья.jpg`
- `tests/tiny_library/日本語の写真.JPG`

## The OCR fixtures (`tests/fixtures/ocr/`)

Drawn with Pillow's `ImageDraw` and its built-in default font: short lines of typed text (dates
such as `1998-07-04`, and words such as `ROLL` and `FILM`) on flat colours, some rotated, plus a
blank page and a solid red square. `tests/fixtures/ocr/truth.json` records, by hand, what each one
shows.

- `tests/fixtures/ocr/blank.png`
- `tests/fixtures/ocr/border.png`
- `tests/fixtures/ocr/colour_blue_on_red.png`
- `tests/fixtures/ocr/colour_red_on_blue.png`
- `tests/fixtures/ocr/date_imprint.png`
- `tests/fixtures/ocr/date_iso.png`
- `tests/fixtures/ocr/date_words.png`
- `tests/fixtures/ocr/inverted.png`
- `tests/fixtures/ocr/mixed.png`
- `tests/fixtures/ocr/order.png`
- `tests/fixtures/ocr/solid_red.png`
- `tests/fixtures/ocr/tilt_3.png`
- `tests/fixtures/ocr/tilt_8.png`

## The face-model parity image

A drawn oval with two eye blobs - not a photograph of a person - generated once by
`_write_fixture` in `tests/test_faces_model.py` and committed with the model outputs it produces.

- `tests/fixtures/faces_parity.png`

## Workspaces an older pam wrote (`tests/fixtures/old_workspaces/`)

Written in pam's development repository by `tools/make_old_workspaces.py` (a development tool that is
not part of the public tree; it needs the pre-1.0 tags), which runs the `v0.1-m13` and `v0.1-m14` tags' own code
from a temporary `git worktree` over a four-file archive of text stand-ins (each file holds the
words "the only copy of" and its own name; the fake exiftool reads typed-in dates from a sidecar).
Each `catalog.db` is that older pam's SQLite catalog, with the temporary folder's path replaced by
the stand-in `C:/Users/user/old-pam-fixture` in every text column. No photograph, person, place or
real path is in any of them. `archive.json` beside each lists the archive's files and mtimes.

- `tests/fixtures/old_workspaces/v0.1-m13/interrupted/catalog.db`
- `tests/fixtures/old_workspaces/v0.1-m13/open/catalog.db`
- `tests/fixtures/old_workspaces/v0.1-m14/interrupted/catalog.db`
- `tests/fixtures/old_workspaces/v0.1-m14/open/catalog.db`
