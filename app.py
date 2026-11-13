import json
import os
from pathlib import Path

import streamlit as st
from pydantic import ValidationError
from domain import Facts, read_csv, local_extract, derived, quality, digest
from provider import MODEL, ProviderError, extract, query as ai_query
from search import Query, propose, search, duplicates
from store import Store

ROOT = Path(__file__).resolve().parent
store = Store(os.environ.get('PROPERTYDATA_DB', str(ROOT / 'private' / 'property.sqlite3')))
st.set_page_config(page_title='PropertyData Copilot', page_icon='🏘️', layout='wide')
st.title('PropertyData Copilot')
st.caption('Listing normalization · Unit-aware filters · Duplicate candidates · Human review')
st.info('Bản local xử lý dữ liệu tin đăng, không định giá hoặc tư vấn đầu tư/pháp lý. Dữ liệu mẫu hoàn toàn giả lập. '
        'Tìm kiếm chỉ dùng tin đã duyệt. Xếp hạng TF-IDF là lexical search, không phải embedding ngữ nghĩa.')
with st.sidebar:
    key = st.text_input('Groq API key — tùy chọn', type='password')
    model = st.text_input('Model ID', value=MODEL)
    consent = st.checkbox('Tôi được phép gửi văn bản đã chọn tới Groq')
    st.caption('Chỉ gửi tin đang chọn hoặc câu tìm kiếm khi bấm AI. Không gửi ảnh. Không lưu key, không tự retry.')
if 'flash' in st.session_state:
    st.error(st.session_state.pop('flash'))
with st.expander('1. Nhập tin đăng', expanded=not store.rows()):
    uploaded = st.file_uploader('CSV UTF-8: id,title,description', type='csv')
    if st.button('Nạp 16 tin mẫu'):
        st.success(store.import_rows(read_csv((ROOT / 'data' / 'listings.csv').read_bytes())))
    if st.button('Nhập CSV đã chọn', disabled=uploaded is None):
        try:
            st.success(store.import_rows(read_csv(uploaded.getvalue())))
        except ValueError as error:
            st.error(str(error))
rows = store.rows()
if not rows:
    st.stop()
st.dataframe([{'id': r['id'], 'title': r['source']['title'], 'state': r['state'], 'revision': r['revision']} for r in rows], hide_index=True, width='stretch')
if st.button('Chuẩn hóa tin pending bằng quy tắc'):
    count = 0
    for row in store.rows():
        if row['state'] == 'pending':
            facts, issues = local_extract(row['source'])
            store.save_result(row['id'], row['revision'], facts, issues, facts, 'local_rules')
            count += 1
    st.session_state['processed'] = count
    st.rerun()
if 'processed' in st.session_state:
    st.caption(f'Đã xử lý {st.session_state["processed"]} tin bằng quy tắc; đây không phải suy luận LLM.')

rows = store.rows()
with st.expander('Ứng viên trùng — không tự ghép/xóa', expanded=False):
    st.dataframe(duplicates(rows), hide_index=True)
    st.caption('Text similarity và dHash chỉ gợi ý kiểm tra; ảnh được dùng lại hoặc hash giống nhau không chứng minh cùng bất động sản.')
identifier = st.selectbox('2. Chọn tin để đối chiếu / duyệt', [row['id'] for row in rows])
row = store.get(identifier)
left, right = st.columns(2)
with left:
    st.write('**Nguồn**')
    st.text(row['source']['title'] + '\n' + row['source']['description'])
    if row['image']:
        st.image(row['image'], width=300, caption='Ảnh gắn với tin; chỉ dùng gợi ý trùng')
    photo = st.file_uploader('Ảnh tin đăng JPG/PNG (tùy chọn)', type=['png', 'jpg', 'jpeg'], key='photo_' + identifier)
    if st.button('Gắn ảnh — yêu cầu duyệt lại', disabled=photo is None):
        try:
            store.attach(identifier, row['revision'], photo.getvalue())
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    if st.button('Trích xuất tin này bằng quy tắc'):
        facts, issues = local_extract(row['source'])
        store.save_result(identifier, row['revision'], facts, issues, facts, 'local_rules')
        st.rerun()
    if st.button('Trích xuất văn bản tin này bằng AI', disabled=not (key and model and consent)):
        try:
            with st.spinner('Đang gửi một tin tới Groq...'):
                facts, issues, raw = extract(row['source'], key, model)
            store.save_result(identifier, row['revision'], facts, issues, raw, 'live:' + model)
            st.rerun()
        except (ProviderError, ValueError) as error:
            store.fail(identifier, row['revision'], str(error))
            st.session_state['flash'] = str(error)
            st.rerun()
with right:
    st.write('**Bản đã lưu** — ' + row['state'])
    if row['error']:
        st.error(row['error'])
    for issue in row['issues']:
        st.warning('Cảnh báo lần trích xuất: ' + issue)
    for issue in quality(row['facts']):
        st.caption(issue)
    st.json(derived(row['facts']))
    st.caption('sale_total_vnd là giá tổng bán; monthly_rent_vnd là thuê/tháng. price_is_derived=True khi nhân giá/m² với diện tích, không phải giá tổng được in trong nguồn.')
    with st.form(f'edit_{identifier}_{row["revision"]}'):
        text = st.text_area('JSON thuộc tính và evidence', json.dumps(row['facts'], ensure_ascii=False, indent=2), height=380)
        saved = st.form_submit_button('Lưu sửa đổi — hủy duyệt cũ')
    if saved:
        try:
            store.edit(identifier, row['revision'], json.loads(text))
            st.rerun()
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            st.error(str(error))
    st.caption('Chỉ sửa theo nguồn đã xác minh. Tin thiếu giá có thể được duyệt, nhưng sẽ không khớp bộ lọc ngân sách. '
               'Khu vực/thành phố là nhãn nguồn, không phải xác nhận ranh giới hành chính.')
    if row['state'] == 'review':
        with st.form(f'approve_{identifier}_{row["revision"]}'):
            reviewer = st.text_input('Tên / bí danh người duyệt', max_chars=100)
            note = st.text_input('Ghi chú đối chiếu nguồn và ứng viên trùng', max_chars=1000)
            checked = st.checkbox('Tôi đã lưu sửa đổi và kiểm tra giá, đơn vị, diện tích, khu vực và ứng viên trùng')
            approved = st.form_submit_button('Duyệt tin đã lưu')
        if approved:
            try:
                store.approve(identifier, row['revision'], reviewer, note, checked)
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    elif row['approval']:
        st.success('Đã duyệt phiên bản hiện tại.')

st.subheader('3. Tìm kiếm tin đã duyệt')
st.caption('Điều kiện cứng hỗ trợ: giao dịch, loại, thành phố, nhãn khu vực, số phòng, diện tích và ngân sách. '
           'Các từ khóa khác chỉ ảnh hưởng xếp hạng, không bảo đảm có tiện ích hoặc thuộc tính ngoài schema.')
approved_rows = store.rows(approved_only=True)
st.caption(f'Hiện có {len(approved_rows)} tin đã duyệt. Bộ lọc cứng luôn được áp dụng, bất kể điểm xếp hạng.')
question = st.text_input('Yêu cầu tìm kiếm', value='Mua căn hộ An Phú TP.HCM dưới 3 tỷ, ít nhất 2 phòng ngủ, từ 65 m2')
question_hash = digest(question)
if st.session_state.get('question_hash') != question_hash:
    st.session_state['question_hash'] = question_hash
    st.session_state.pop('proposal', None)
    st.session_state.pop('confirmed_query', None)
if st.button('Đề xuất bộ lọc bằng quy tắc'):
    st.session_state.pop('confirmed_query', None)
    try:
        proposal, warnings = propose(question, [r['facts']['area_name'] for r in rows])
        st.session_state['proposal'] = proposal
        st.session_state['query_warnings'] = warnings
    except ValueError as error:
        st.error(str(error))
if st.button('Đề xuất bộ lọc bằng AI', disabled=not (key and model and consent)):
    st.session_state.pop('confirmed_query', None)
    try:
        st.session_state['proposal'] = ai_query(question, key, model)
        st.session_state['query_warnings'] = ['AI chỉ đề xuất; hãy kiểm tra tất cả trường trước khi xác nhận.']
    except ProviderError as error:
        st.error(str(error))
proposal = st.session_state.get('proposal')
if proposal:
    for warning in st.session_state.get('query_warnings', []):
        st.warning(warning)
    with st.form('filters_' + digest(proposal)):
        purpose_options, type_options = ['', 'sale', 'rent'], ['', 'apartment', 'house', 'land']
        purpose = st.selectbox('purpose: sale=bán, rent=thuê/tháng', purpose_options, index=purpose_options.index(proposal['purpose'] or ''))
        property_type = st.selectbox('property_type', type_options, index=type_options.index(proposal['property_type'] or ''))
        city = st.text_input('city: ho_chi_minh, ha_noi hoặc nhãn nguồn', value=proposal['city'] or '')
        location = st.text_input('area_name: nhãn khu vực, có dấu như nguồn', value=proposal['area_name'] or '')
        minimum_bed = st.text_input('Số phòng ngủ tối thiểu — trống nếu không lọc', value='' if proposal['min_bedrooms'] is None else str(proposal['min_bedrooms']))
        maximum_bed = st.text_input('Số phòng ngủ tối đa — trống nếu không lọc', value='' if proposal['max_bedrooms'] is None else str(proposal['max_bedrooms']))
        minimum_area = st.text_input('Diện tích tối thiểu m²', value=proposal['min_area_m2'] or '')
        area_operator = st.selectbox('Điều kiện diện tích', ['gte', 'gt'], index=['gte', 'gt'].index(proposal['area_operator']))
        budget = st.text_input('Ngân sách tối đa bằng VND — không dùng tỷ/triệu ở ô này', value=proposal['max_budget_vnd'] or '')
        budget_operator = st.selectbox('Điều kiện ngân sách: lt là <, lte là ≤', ['lt', 'lte'], index=['lt', 'lte'].index(proposal['budget_operator']))
        confirmed = st.form_submit_button('Xác nhận bộ lọc và tìm')
    if confirmed:
        st.session_state.pop('confirmed_query', None)
        try:
            confirmed_query = Query(purpose=purpose or None, property_type=property_type or None, city=city.strip() or None,
                area_name=location.strip() or None, min_bedrooms=int(minimum_bed) if minimum_bed.strip() else None,
                max_bedrooms=int(maximum_bed) if maximum_bed.strip() else None,
                min_area_m2=minimum_area.strip() or None, area_operator=area_operator,
                max_budget_vnd=budget.strip() or None, budget_operator=budget_operator, keywords=proposal['keywords']).model_dump()
            search(approved_rows, confirmed_query)
            st.session_state['confirmed_query'] = confirmed_query
        except (ValueError, ValidationError) as error:
            st.error(str(error))
if st.session_state.get('confirmed_query'):
    st.write('**Bộ lọc đã xác nhận**')
    st.json(st.session_state['confirmed_query'], expanded=False)
    result = search(approved_rows, st.session_state['confirmed_query'])
    st.write('Số kết quả:', len(result))
    st.dataframe(result, hide_index=True, width='stretch')
    st.caption('rank_score là cosine TF-IDF, không phải xác suất khớp hay chất lượng bất động sản. Giá tổng suy ra không gồm thuế/phí và chỉ là phép nhân dữ liệu nguồn.')

st.subheader('4. Xuất danh mục đã duyệt')
try:
    st.download_button('Tải ZIP tin đã duyệt', store.export(), 'propertydata-reviewed.zip', 'application/zip')
except ValueError as error:
    st.caption(str(error))
st.caption('ZIP có reviewed-listings.csv và audit.json. Database/ảnh/event lưu private/. '
           'Tên người duyệt là nhãn, không xác thực; chưa có đăng nhập nhiều người hoặc tự đăng tin lên website khác.')
