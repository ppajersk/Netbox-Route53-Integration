from datetime import datetime, timedelta
import logging
import json
import os
import sys
import pynetbox
import boto3

# Either manually enter the necessary keys or set them as environment variables. The latter is recommended and examples are provided
# Export Netbox: url, timespan & token....Examples:
# export NETBOX_URL=https://example.net
# export NETBOX_TOKEN=guyg3r2fw8e7tgf2898366487n
# export NETBOX_TIMESPAN=1   (<- value in days)

# Export Route53: access_key_id, secret_access_key, HostZoneId....Examples:
# export ROUTE53_ID=KUYGDS783WSKI
# export ROUTE53_KEY=JHIU243YT9F8UHSUY983Y
# export ROUTE53_HOSTEDZONE_ID=EROTIJGOI438979800BW
# export ROUTE53_TAG="nbr53" (note: the " " are necessary here)
# Note: these are made up keys and are not valid


class NetboxRoute53:
    def __init__(self):
        logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
        self.logging = logging.getLogger()

        # Initialize Netbox
        if "NETBOX_URL" in os.environ:
            self.nb_url = os.getenv("NETBOX_URL")
        else:
            logging.error("Environmnet variable NETBOX_URL must be set")
            sys.exit(1)

        if "NETBOX_TOKEN" in os.environ:
            self.nb_token = os.getenv("NETBOX_TOKEN")
        else:
            logging.error("Environmnet variable NETBOX_TOKEN must be set")
            sys.exit(1)

        if "NETBOX_TIMESPAN" in os.environ:
            self.timespan = int(os.getenv("NETBOX_TIMESPAN"))
        else:
            self.timespan = 2

        self.nb = pynetbox.api(url=self.nb_url, token=self.nb_token)
        self.nb_ip_addresses = self.nb.ipam.ip_addresses.all()

        # Initialize Route53
        if "ROUTE53_ID" in os.environ:
            self.r53_id = os.getenv("ROUTE53_ID")
        else:
            logging.error("Environment variable ROUTE53_ID must be set")
            sys.exit(1)

        if "ROUTE53_KEY" in os.environ:
            self.r53_key = os.getenv("ROUTE53_KEY")
        else:
            logging.error("Environment variable ROUTE53_KEY must be set")
            sys.exit(1)

        if "ROUTE53_HOSTEDZONE_ID" in os.environ:
            self.r53_zone_id = os.getenv("ROUTE53_HOSTEDZONE_ID")
        else:
            logging.error(
                "Environment variable ROUTE53_HOSTEDZONE_ID must be set")
            sys.exit(1)

        if "ROUTE53_TAG" in os.environ:
            self.r53_tag = os.getenv("ROUTE53_TAG")
        else:
            self.r53_tag = "\"nbr53\""

        # initiate connection to Route53 Via Boto3
        self.client = boto3.client(
            'route53', aws_access_key_id=self.r53_id, aws_secret_access_key=self.r53_key)

        # Get hosted_zone domain name for appending to record names
        Hosted_zone_response = self.client.get_hosted_zone(Id=self.r53_zone_id)
        HZ = json.dumps(Hosted_zone_response)
        HZ1 = json.loads(HZ)
        self.HZ_Name = HZ1['HostedZone']['Name']

        self.R53_Record_response = self.client.list_resource_record_sets(
            HostedZoneId=self.r53_zone_id)
        self.r53_tag_dict = {}
        self.r53_ip_dict = {}

    def get_nb_records(self, nb_timespan):
        timespan = datetime.today() - timedelta(days=nb_timespan)
        timespan.strftime('%Y-%m-%dT%XZ')
        ip_search = self.nb.ipam.ip_addresses.filter(
            within=self.nb_ip_addresses, last_updated__gte=timespan)
        return ip_search

    def check_record_exists(self, dns, ip):
        values = [
            value for value in self.R53_Record_response['ResourceRecordSets']
            if dns in value['Name'] or ip in value['ResourceRecords'][0]['Value'] if value['Type'] == 'A'
        ]
        if len(values) > 0:
            return True
        return False

    def route53_tag_creator(self, id):
        tag = self.r53_tag
        tag_strip = tag.strip('"')
        return_tag = '"' + tag_strip + " " + id + '"'
        return(return_tag)

    def get_r53_record_tag(self, id):
        if id in self.r53_tag_dict:
            return True
        return False

    def get_r53_records(self):
        for r53_record in self.R53_Record_response['ResourceRecordSets']:
            R53_tag = r53_record['ResourceRecords'][0]['Value']
            R53_Record_name = r53_record['Name']
            R53_Record_type = r53_record['Type']
            if R53_Record_type == 'TXT':
                sep = ' '
                tag = R53_tag.split(sep, 1)[0] + '"'
                id = R53_tag.split(sep, 1)[1]
                id = id.strip('"')
                if tag == self.r53_tag:
                    self.r53_tag_dict.update({id: R53_Record_name})

        for r53_record in self.R53_Record_response['ResourceRecordSets']:
            R53_tag = r53_record['ResourceRecords'][0]['Value']
            R53_Record_name = r53_record['Name']
            R53_Record_type = r53_record['Type']
            if R53_Record_type == 'A':
                if R53_Record_name in self.r53_tag_dict.values():
                    ip = r53_record['ResourceRecords'][0]['Value']
                    self.r53_ip_dict.update({R53_Record_name: ip})

    def verify_and_update(self, dns, ip, id, tag):
        R53_Record_name = self.r53_tag_dict[id]
        R53_ip = self.r53_ip_dict[R53_Record_name]

        if R53_Record_name == dns and R53_ip == ip:
            self.logging.debug("Record is a complete match")
        elif R53_Record_name != dns and R53_ip == ip:
            self.logging.debug("Dns does not match")
            self.delete_r53_record(R53_Record_name, ip, tag)
            self.create_r53_record(dns, ip, tag)
            self.logging.debug("Record cleaned")
        elif R53_Record_name == dns and R53_ip != ip:
            self.logging.debug("Ip does not match")
            self.logging.debug("Updating record")
            self.update_r53_record(R53_Record_name, ip)

    def update_r53_record(self, dns, ip):
        self.client.change_resource_record_sets(
            HostedZoneId=self.r53_zone_id,
            ChangeBatch={
                'Comment':
                '',
                'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': dns,
                            'Type': 'A',
                            'TTL': 123,
                            'ResourceRecords': [
                                {
                                    'Value': ip,
                                },
                            ],
                        }
                    },
                ]
            }
        )

    def create_r53_record(self, dns, ip, tag):
        self.client.change_resource_record_sets(
            HostedZoneId=self.r53_zone_id,
            ChangeBatch={
                'Changes': [{
                    'Action': 'CREATE',
                    'ResourceRecordSet': {
                        'Name': dns,
                        'Type': 'A',
                        'TTL': 123,
                        'ResourceRecords': [{
                            'Value': ip
                        }]
                    }
                }, {
                    'Action': 'CREATE',
                    'ResourceRecordSet': {
                        'Name': dns,
                        'Type': 'TXT',
                        'TTL': 123,
                        'ResourceRecords': [{
                            'Value': tag
                        }]
                    }
                }]
            }
        )

    def delete_r53_record(self, dns, ip, tag):
        self.client.change_resource_record_sets(
            HostedZoneId=self.r53_zone_id,
            ChangeBatch={
                'Comment':
                '',
                'Changes': [{
                    'Action': 'DELETE',
                    'ResourceRecordSet': {
                        'Name': dns,
                        'Type': 'A',
                        'TTL': 123,
                        'ResourceRecords': [{
                            'Value': ip
                        }]
                    }
                }, {
                    'Action': 'DELETE',
                    'ResourceRecordSet': {
                        'Name': dns,
                        'Type': 'TXT',
                        'TTL': 123,
                        'ResourceRecords': [{
                            'Value': tag
                        }]
                    }
                }]
            }
        )

    # In the case that a dns name is changed, and the script is run, clean_r53_records wont
    # find that dns and won't attempt to clean it. Running the script again will clean it
    def clean_r53_records(self):
        self.logging.debug("...Record cleaning...")
        self.get_r53_records()

        nb_ip_list = {}
        ip_search = self.nb.ipam.ip_addresses.filter(
            within=self.nb_ip_addresses)
        for i in ip_search:
            nb_ip_list.update({str(i.id): i})

        if nb_ip_list != {}:
            for record in self.r53_tag_dict:
                R53_Record_name = self.r53_tag_dict[record]
                R53_ip = self.r53_ip_dict[R53_Record_name]
                R53_tag = self.route53_tag_creator(record)
                if record in nb_ip_list:
                    self.logging.debug("Record exists %s", R53_Record_name)
                else:
                    self.logging.debug("Purging record %s", R53_Record_name)
                    self.delete_r53_record(R53_Record_name, R53_ip, R53_tag)
        else:
            self.logging.debug("Netbox recordset is empty %s")

    # Check all records in Netbox against Route53, and update the tagged record's ip/dns pair if they are incorrect
    def integrate_records(self, event):
        try:
            nb_timespan = json.loads((event["Timespan"]))
        except:
            nb_timespan = self.timespan
            pass

        self.get_r53_records()
        self.logging.debug("Record integration...")
        for i in self.get_nb_records(nb_timespan):
            nb_id = str(i.id)
            nb_dns = i.dns_name + "." + self.HZ_Name
            ip = str(i)
            sep = '/'
            nb_ip = ip.split(sep, 2)[0]
            nb_tag = self.route53_tag_creator(nb_id)
            self.logging.debug("Checking %s", nb_ip + " " + nb_dns)
            #if self.check_record_exists(nb_dns, nb_ip):
            if self.get_r53_record_tag(nb_id):
                self.verify_and_update(nb_dns, nb_ip, nb_id, nb_tag)
            else:
                self.logging.debug("Adding %s", nb_ip)
                try:
                    self.create_r53_record(nb_dns, nb_ip, nb_tag)
                except:
                    pass
                    self.logging.debug("Error adding record, most likely a duplicate")


        self.clean_r53_records()

    # Create/update/delete a single netbox record based on webhook request
    def webhook_update_record(self, event):
        self.get_r53_records()
        testjson = json.loads((event["body"]))
        request_type = testjson['event']
        request_ip = testjson['data']['address']
        request_dns = testjson['data']['dns_name']
        request_id = str(testjson['data']['id'])
        nb_dns = request_dns + "." + self.HZ_Name
        ip = str(request_ip)
        sep = '/'
        nb_ip = ip.split(sep, 2)[0]
        nb_tag = self.route53_tag_creator(request_id)
        rec_status = self.get_r53_record_tag(request_id)

        if rec_status:
            if request_type == 'updated':
                self.logging.debug("Updating %s", request_dns)
                self.verify_and_update(nb_dns, nb_ip, request_id, nb_tag)
            elif request_type == 'deleted':
                self.logging.debug("Deleting %s", request_dns)
                self.delete_r53_record(nb_dns, nb_ip, nb_tag)
            else:
                self.logging.debug("Record already exists: %s", request_dns)
        elif request_type == 'created':
            self.logging.debug("Creating %s", request_dns)
            self.create_r53_record(nb_dns, nb_ip, nb_tag)
        else:
            self.logging.debug("Record does not exist: %s", request_dns)
