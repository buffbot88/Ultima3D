;; export_png.scm — save a PNG copy of %%INPUT%% as %%OUTPUT%%, source untouched.
;; The PNG saver flattens on write, so layers survive in the original.
(let* ((img (car (gimp-file-load RUN-NONINTERACTIVE %%INPUT%% %%INPUT%%))))
  (let ((layer (car (gimp-image-get-active-layer img))))
    (gimp-file-save RUN-NONINTERACTIVE img layer %%OUTPUT%% %%OUTPUT%%))
  (gimp-image-delete img))
