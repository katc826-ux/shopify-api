from shopify_client import send_weekly_dji_consumer_sales_report


def main():
    print("Weekly DJI consumer report run started")
    result = send_weekly_dji_consumer_sales_report(days=7)

    report = result["report"]
    print(report["markdown_table"])

    if result["sent"]:
        print(f"Weekly DJI consumer report emailed to {result['destination']}")
    else:
        print(f"Weekly DJI consumer report not emailed: {result['reason']}")

    print("Weekly DJI consumer report run finished")


if __name__ == "__main__":
    main()
