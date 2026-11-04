;; prep_image.scm — normalize %%INPUT%% to flat RGB 8-bit, save as %%OUTPUT%%.
;; Runs inside batch GIMP via gimp_run_script; the source file is untouched.
(let* ((img (car (gimp-file-load RUN-NONINTERACTIVE %%INPUT%% %%INPUT%%))))
  (gimp-image-convert-rgb img)
  (gimp-image-convert-precision img PRECISION-U8-GAMMA)
  (gimp-image-flatten img)
  (let ((layer (car (gimp-image-get-active-layer img))))
    (gimp-file-save RUN-NONINTERACTIVE img layer %%OUTPUT%% %%OUTPUT%%))
  (gimp-image-delete img))
