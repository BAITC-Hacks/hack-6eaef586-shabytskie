import pandas as pd

from frontend.review import build_draft, missing_override_comments, review_signature


def test_manual_quantity_override_requires_comment_and_draft_is_traceable():
    edited=pd.DataFrame({
        "sku":["A","B"],"supplier":["S1","S2"],"selected":[True,False],
        "recommended_order":[10.0,5.0],"final_quantity":[12.0,5.0],"comment":["",""]
    })
    assert missing_override_comments(edited) == ["A"]
    edited.loc[0,"comment"]="Менеджер увеличил страховой запас"
    draft=build_draft(edited,{"run_id":"run-1","engine_mode":"integrated","data_origin":"uploaded"})
    assert draft.sku.tolist() == ["A"]
    assert draft.loc[0,"original_recommended_order"] == 10.0
    assert draft.loc[0,"run_id"] == "run-1"


def test_signature_changes_when_comment_changes():
    state={("run-1","A"):{"selected":True,"quantity":10,"comment":"до"}}
    before=review_signature(state,"run-1")
    state[("run-1","A")]["comment"]="после"
    assert review_signature(state,"run-1") != before

