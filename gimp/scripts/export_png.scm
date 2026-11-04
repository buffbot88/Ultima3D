;; export_png.scm — save a copy of %%INPUT%% as %%OUTPUT%%, source untouched.
;; %%FLATTENSAVE%% is filled per detected GIMP major version: 2.x saves the
;; flattened drawable, 3.x saves the image directly (no drawable argument).
(let* ((img (car (gimp-file-load RUN-NONINTERACTIVE %%INPUT%% %%INPUT%%))))
  %%FLATTENSAVE%%
  (gimp-image-delete img))
