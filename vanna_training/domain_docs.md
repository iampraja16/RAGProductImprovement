# EMR Database Vanna Domain Documentation

The `emr_records` table contains Equipment Maintenance Records for heavy machinery.

## Critical Column Usage Rules

- `machine_model` is the SPECIFIC equipment model with suffix (e.g., 'HD785-7', 'PC200-10M0', 'D155A-6'). 
  ALWAYS use this column when the user mentions a model name with a dash and number suffix like HD785-7 or PC200-8.
- `model_family` is the BROAD category WITHOUT suffix (e.g., 'HD785', 'PC200', 'D155A'). 
  Only use this column when the user asks about a general family like "semua model PC200" without specifying the exact variant.
- `branch_site` is the location where the equipment is operated.
- `graph_community_summary` contains the executive summary from the GraphRAG pipeline. 
  When filtering for records that HAVE a GraphRAG summary, use: `graph_community_summary IS NOT NULL`.
  When searching for specific topics in the summary, use: `graph_community_summary ILIKE '%keyword%'`.
- `techcare_component` is the main component or subsystem where the failure occurred (e.g., 'FINAL DRIVE', 'SWING MOTOR').
  Use this when the user asks about a specific component or wants to know which components fail most often.
- `techcare_sub_component` is the specific sub-part of the component (e.g., 'Floating Seal', 'Injector').
- Use `created_date` when filtering by month or year.
