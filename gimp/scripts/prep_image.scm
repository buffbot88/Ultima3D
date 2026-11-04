;; prep_image.scm — normalize %%INPUT%% to flat RGB 8-bit, save as %%OUTPUT%%.
;; Runs inside batch GIMP via gimp_run_script; the source file is untouched.
;; Conversions are conditional: converting an image that already has the
;; target type fails loudly, so convert only when needed. %%BASETYPE%% and
;; %%FLATTENSAVE%% are filled per detected GIMP major version (3.x renamed
;; several procedures and dropped the drawable from gimp-file-save).
(let* ((img (car (gimp-file-load RUN-NONINTERACTIVE %%INPUT%% %%INPUT%%))))
  (if (not (= (car (%%BASETYPE%% img)) RGB-IMAGE))
      (gimp-image-convert-rgb img))
  (if (> (car (gimp-image-get-precision img)) 150)
      (gimp-image-convert-precision img PRECISION-U8-GAMMA))
  %%FLATTENSAVE%%
  (gimp-image-delete img))
