"""Streamlit web app: Appliance Catalogue → eProcess Transformer (Steps 1 & 2).

Run with:  streamlit run src/app.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / 'adapters'))

import streamlit as st
from transformer import transform_files
from enricher import enrich_workbook, get_blank_rows, summarise_blank_rows

import openpyxl
import io

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title='Catalogue → eProcess Transformer',
    page_icon='🔄',
    layout='wide',
)

st.title('Appliance Catalogue → eProcess Transformer')
tab1, tab2 = st.tabs(['Step 1: Transform Catalogue', 'Step 2: Enrich Dimensions'])

# ===========================================================================
# TAB 1 — catalogue → eProcess format
# ===========================================================================
with tab1:
    st.markdown(
        'Upload one or more **brand master catalogue** `.xlsx` files and click **Transform** '
        'to produce a single eProcess-format workbook ready for M10 import.'
    )

    uploaded_files = st.file_uploader(
        'Select catalogue files (one per brand)',
        type=['xlsx'],
        accept_multiple_files=True,
        key='step1_upload',
        help='Row 4 must be the column header row; rows 1-3 are the title banner.',
    )

    if uploaded_files:
        st.caption(f'{len(uploaded_files)} file(s) selected:')
        for f in uploaded_files:
            st.caption(f'  - {f.name}')

    if st.button('Transform', disabled=not uploaded_files, type='primary', key='step1_btn'):
        with st.spinner('Reading and mapping rows...'):
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                input_paths = []
                for f in uploaded_files:
                    dest = tmp / f.name
                    dest.write_bytes(f.getvalue())
                    input_paths.append(dest)
                output_path = tmp / 'eprocess_import.xlsx'
                try:
                    summary = transform_files(input_paths, output_path)
                    st.session_state['s1_summary'] = summary
                    st.session_state['s1_bytes']   = output_path.read_bytes()
                except Exception as exc:
                    st.error(f'Transform failed: {exc}')
                    st.stop()

    if 's1_summary' in st.session_state:
        summary = st.session_state['s1_summary']
        st.divider()
        st.subheader('Transform Summary')

        total_in = sum(v['total_rows'] for v in summary['files'].values())
        c1, c2, c3 = st.columns(3)
        c1.metric('Source rows read', total_in)
        c2.metric('Rows mapped',      summary['total_mapped'])
        c3.metric('Rows unmapped',    summary['total_unmapped'])

        if len(summary['files']) > 1:
            with st.expander('Rows per source file'):
                for fname, fdata in summary['files'].items():
                    st.write(f'**{fname}** — {fdata["total_rows"]} rows')

        mapped_nz = {k: v for k, v in summary['mapped_by_sheet'].items() if v > 0}
        if mapped_nz:
            with st.expander('Mapped rows by output sheet', expanded=True):
                for sheet, count in mapped_nz.items():
                    st.write(f'- **{sheet}**: {count}')

        st.info(
            f'**Missing dimensions:** {summary["missing_dimensions"]} rows have blank '
            'Height / Width / Depth (amber cells in output). '
            'Use Step 2 to enrich these automatically.'
        )

        if summary['unmapped_by_reason']:
            with st.expander('Unmapped rows by reason', expanded=True):
                st.caption('These rows appear in the "Unmapped - Review" sheet.')
                for reason, count in summary['unmapped_by_reason'].items():
                    st.write(f'- {reason}: **{count}** row(s)')
        else:
            st.success('All rows were successfully mapped.')

        st.divider()
        st.download_button(
            label='Download eProcess Import File',
            data=st.session_state['s1_bytes'],
            file_name='eprocess_import.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            type='primary',
        )

# ===========================================================================
# TAB 2 — dimension enrichment
# ===========================================================================
with tab2:
    st.markdown(
        'Upload (or use Step 1 output directly) to fill in **Height / Width / Depth** '
        'by fetching each product page. LG NZ is the only live adapter; other brands '
        'return *Not Found* until their adapters are built.'
    )

    # --- Input: Step 1 output file (or chained from session) ---------------
    chain_from_step1 = (
        'Use Step 1 output from this session'
        if 'step2_enriched_bytes' not in st.session_state and 's1_bytes' in st.session_state
        else None
    )

    upload_col, option_col = st.columns([3, 2])
    with upload_col:
        enrich_upload = st.file_uploader(
            'Upload Step 1 output .xlsx',
            type=['xlsx'],
            key='step2_upload',
        )
    with option_col:
        if chain_from_step1:
            use_chained = st.checkbox('Use Step 1 output from this session', value=True)
        else:
            use_chained = False

    # Resolve which bytes to enrich
    if use_chained and 's1_bytes' in st.session_state:
        input_bytes = st.session_state['s1_bytes']
        input_label = 'Step 1 output (from this session)'
    elif enrich_upload:
        input_bytes = enrich_upload.getvalue()
        input_label = enrich_upload.name
    else:
        input_bytes = None
        input_label = None

    if input_bytes:
        st.caption(f'Input: {input_label}')

    # --- Pre-run summary ---------------------------------------------------
    if input_bytes and 'enrich_rows_preview' not in st.session_state:
        try:
            wb_preview = openpyxl.load_workbook(io.BytesIO(input_bytes))
            preview_rows = get_blank_rows(wb_preview)
            st.session_state['enrich_rows_preview'] = summarise_blank_rows(preview_rows)
        except Exception:
            pass

    if 'enrich_rows_preview' in st.session_state and input_bytes:
        preview = st.session_state['enrich_rows_preview']
        st.subheader('Rows needing enrichment')
        st.metric('Total blank rows', preview['total'])

        if preview['by_sheet']:
            cols = st.columns(min(len(preview['by_sheet']), 3))
            for i, (sheet, count) in enumerate(preview['by_sheet'].items()):
                cols[i % 3].metric(sheet, count)

        if preview['by_brand']:
            with st.expander('Blank rows by brand'):
                for brand, count in sorted(preview['by_brand'].items()):
                    st.write(f'- **{brand}**: {count}')

    # --- Options & Run button ----------------------------------------------
    force_retry = st.checkbox(
        'Retry previously unresolved rows',
        value=False,
        help='Re-fetches URLs that were cached as Needs Review or Not Found.',
    )

    run_disabled = not input_bytes or (
        'enrich_rows_preview' in st.session_state
        and st.session_state['enrich_rows_preview']['total'] == 0
    )

    if st.button('Run Enrichment', disabled=run_disabled, type='primary', key='step2_btn'):
        st.session_state.pop('enrich_rows_preview', None)   # force re-parse on rerun

        total_rows = 0
        if 'enrich_rows_preview' in st.session_state:
            total_rows = st.session_state['enrich_rows_preview']['total']

        progress_bar = st.progress(0.0)
        status_text  = st.empty()

        done_count = [0]
        result_log: list[dict] = []

        def _progress(done, total, row, result):
            done_count[0] = done
            frac = done / total if total else 1.0
            progress_bar.progress(frac)
            status_text.text(
                f'{done}/{total}: {row.brand} {row.sku} ({row.sheet}) — {result.confidence}'
            )
            result_log.append({
                'sheet':      row.sheet,
                'brand':      row.brand,
                'sku':        row.sku,
                'confidence': result.confidence,
                'source_url': result.source_url,
                'raw_text':   result.raw_text,
                'reason':     result.reason,
            })

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / 'eprocess_enriched.xlsx'
            try:
                rows, results = enrich_workbook(
                    input_bytes,
                    out_path,
                    force_retry=force_retry,
                    progress_cb=_progress,
                )
                enriched_bytes = out_path.read_bytes()
                st.session_state['step2_enriched_bytes'] = enriched_bytes
                st.session_state['step2_result_log']     = result_log
                st.session_state['step2_total']          = len(rows)
            except Exception as exc:
                st.error(f'Enrichment failed: {exc}')
                st.stop()

        progress_bar.progress(1.0)
        status_text.text('Done.')

    # --- Post-run summary + review table -----------------------------------
    if 'step2_result_log' in st.session_state:
        log = st.session_state['step2_result_log']
        st.divider()
        st.subheader('Enrichment Results')

        resolved    = [r for r in log if r['confidence'] == 'Resolved']
        needs_review= [r for r in log if r['confidence'] == 'Needs Review']
        not_found   = [r for r in log if r['confidence'] == 'Not Found']

        rc1, rc2, rc3 = st.columns(3)
        rc1.metric('Resolved',     len(resolved))
        rc2.metric('Needs Review', len(needs_review))
        rc3.metric('Not Found',    len(not_found))

        # By-brand breakdown
        if log:
            brand_stats: dict[str, dict[str, int]] = {}
            for r in log:
                b = r['brand']
                brand_stats.setdefault(b, {'Resolved': 0, 'Needs Review': 0, 'Not Found': 0})
                brand_stats[b][r['confidence']] += 1
            with st.expander('Results by brand'):
                for brand, stats in sorted(brand_stats.items()):
                    st.write(
                        f'**{brand}**: '
                        f'{stats["Resolved"]} resolved, '
                        f'{stats["Needs Review"]} needs review, '
                        f'{stats["Not Found"]} not found'
                    )

        # Review table
        to_review = needs_review + not_found
        if to_review:
            import pandas as pd
            with st.expander(f'Rows needing manual review ({len(to_review)})', expanded=True):
                st.caption(
                    'These rows still have blank or amber dimensions in the output. '
                    'Open the Link to fill them manually.'
                )
                df = pd.DataFrame(to_review)[
                    ['brand', 'sku', 'sheet', 'confidence', 'source_url', 'reason', 'raw_text']
                ].rename(columns={
                    'brand':      'Brand',
                    'sku':        'SKU',
                    'sheet':      'Sheet',
                    'confidence': 'Confidence',
                    'source_url': 'Source URL',
                    'reason':     'Reason',
                    'raw_text':   'Raw Text',
                })
                st.dataframe(df, use_container_width=True)

        st.divider()
        st.download_button(
            label='Download Enriched Workbook',
            data=st.session_state['step2_enriched_bytes'],
            file_name='eprocess_enriched.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            type='primary',
        )
