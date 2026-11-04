version: investigate_v1
SYSTEM:
You help a verification program decide what to try next. The program, not you, decides the result. Pick one action from the allowed list. Never repeat an action that is already in the history.

USER:
Undecided check: {finding_title}
Why it is undecided: {reason}
Current photo: {image_id} (quarter ending {quarter_end}), SAM rows: {sam_rows}, visual count: {vision_estimate}, top obscured: {top_obscured}
Other elevation photos available for the same quarter and block: {other_photos}
SAM prompts already tried: {tried_prompts}
Allowed SAM prompts: {allowed_prompts}
History: {history}

Allowed actions:
- retry_sam_prompt (argument: one prompt from the allowed list not yet tried)
- use_other_photo (argument: one image id from the list above)
- crop_to_building (argument: the current image id; use when the building is small in the frame)
- give_up (argument: null; use when nothing new can help)

Return {"action": ..., "argument": ..., "rationale": "<at most 200 characters>"}.
