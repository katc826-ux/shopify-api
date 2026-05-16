# shopify-api
API call to Shopify to track store inventory and top selling items

## Sold products by order number

Use this endpoint when a custom GPT has one or more Shopify order numbers and needs
the products sold on those orders:

`POST /orders/sold_products`

```json
{
  "order_numbers": ["#1001", "1002"]
}
```

The response includes product title, quantity, unit price, total before tax,
tax total, total after tax, currency, and whether each item was refunded.

## Weekly DJI consumer sales report

This project now includes a weekly DJI consumer sales report that returns:

- net sold units
- product variant SKU
- product title
- inventory on hand

### On-demand API

`GET /reports/weekly_dji_consumer_sales?days=7`

You can also request an explicit date range:

`GET /reports/weekly_dji_consumer_sales?start_date=2026-04-01&end_date=2026-04-07`

### Weekly scheduler script

Run:

```bash
python process_weekly_dji_report.py
```

If SMTP is configured, the script emails the report to `sales@speedydrone.ca` by default. Otherwise it prints the markdown table to stdout.

### Optional environment variables

- `REPORT_EMAIL_TO`: destination email, default `sales@speedydrone.ca`
- `SMTP_HOST`: SMTP server host
- `SMTP_PORT`: SMTP server port, default `587`
- `SMTP_USERNAME`: SMTP login username
- `SMTP_PASSWORD`: SMTP login password
- `SMTP_USE_TLS`: set to `false` to disable STARTTLS
- `SMTP_FROM_EMAIL`: sender email address shown on the report
- `DJI_CONSUMER_INCLUDE_KEYWORDS`: comma-separated include keywords, default `dji,osmo`
- `DJI_CONSUMER_EXCLUDE_KEYWORDS`: comma-separated exclude keywords used to skip enterprise/non-consumer lines
