%# Model download / export UI, included from the Model group of detection.tpl.
%# Lists catalogued models that aren't on disk and offers a one-click export
%# (ultralytics downloads the weights + converts to ONNX into the storage
%# models folder). Inputs here are export-action params, not config fields, so
%# they live in this separate partial (exempt from the FIELD_RULES consistency
%# check by filename) and carry no hx-validate markup.
% unavailable = catalogue_unavailable if defined('catalogue_unavailable') else []
% extras = extras_installed if defined('extras_installed') else {}
% running = di_running if defined('di_running') else False
% export_installed = extras.get('export', False)
% if unavailable:
    <div class="group">
        <h3 class="group-title">{{_('Download Model')}}</h3>
        <span class="section-note">
            {{_('Export a YOLO model to ONNX into the storage folder. Needs the model-export tools and an internet connection.')}}
        </span>
%     if not export_installed:
        <p class="section-note">{{_('Install the model-export tools (run')}} <code>install-detection.sh --with-export</code>{{_(') to enable downloads.')}}</p>
%     end
%     if export_installed:
        <div class="row">
            <div class="field">
                <label>{{_('Model')}}</label>
                <select id="detection-export-model" name="export_model">
%         for value, label in unavailable:
                    <option value="{{value}}">{{label}}</option>
%         end
                </select>
            </div>
            <div class="field">
                <label>{{_('Export image size')}}</label>
                <input id="detection-export-imgsz" type="number" name="imgsz" value="{{config.detection.inference_size}}" min="160" max="1280" step="32">
                <span class="field-note">{{_('Snapped to a multiple of 32; match the Inference Size you run.')}}</span>
            </div>
        </div>
        <div class="row">
            <div class="field">
                <button type="button" class="save-btn"
                        {{'disabled' if running else ''}}
                        hx-post="/section/detection/export"
                        hx-include="#detection-export-model, #detection-export-imgsz"
                        hx-target="#detection-section"
                        hx-swap="outerHTML">
                    {{_('Download model')}}
                </button>
            </div>
        </div>
%     end
    </div>
% end
