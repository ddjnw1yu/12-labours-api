import re

from fastapi import HTTPException

from sgqlc.operation import Operation
from app.sgqlc_schema import Query


BAD_REQUEST = 400
UNAUTHORIZED = 401
NOT_FOUND = 404
METHOD_NOT_ALLOWED = 405
INTERNAL_SERVER_ERROR = 500


class SimpleGraphQLClient:
    def add_count_field(self, item, query):
        # Add default count field to query
        count_field = f"total: _{item.node}_count"
        if item.filter != {}:
            # Manually modify and add count filed into graphql query
            filter_argument = re.sub(
                '\'([_a-z]+)\'', r'\1', re.sub('\{([^{].*[^}])\}', r'\1', f"{item.filter}"))
            count_field = re.sub(
                '\'', '\"', f"total: _{item.node}_count(" + filter_argument + ")")
        return query + count_field

    def convert_query(self, item, query):
        # Convert camel case to snake case
        snake_case_query = re.sub(
            '_[A-Z]', lambda x:  x.group(0).lower(), re.sub('([a-z])([A-Z])', r'\1_\2', str(query)))
        # This is used to update the filter query to fit with the Gen3 graphql format
        if "filter" in item.node:
            snake_case_query = re.sub("_filter", "", snake_case_query)
            item.node = re.sub("_filter", "", item.node)
        # Only pagination graphql will need to add count field
        if type(item.search) == dict:
            snake_case_query = self.add_count_field(item, snake_case_query)
        return "{" + snake_case_query + "}"

    def generate_query(self, item):
        query = Operation(Query)
        if item.node == "experiment":
            if "submitter_id" in item.filter:
                experiment_query = self.convert_query(
                    item,
                    query.experiment(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        submitter_id=item.filter["submitter_id"]
                    )
                )
            else:
                experiment_query = self.convert_query(
                    item,
                    query.experiment(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                    )
                )
            return experiment_query
        elif item.node == "dataset_description":
            if "submitter_id" in item.filter:
                dataset_description_query = self.convert_query(
                    item,
                    query.datasetDescription(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        submitter_id=item.filter["submitter_id"]
                    )
                )
            else:
                dataset_description_query = self.convert_query(
                    item,
                    query.datasetDescription(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                    )
                )
            return dataset_description_query
        elif item.node == "dataset_description_filter":
            dataset_description_filter_query = self.convert_query(
                item,
                query.datasetDescriptionFilter(
                    first=item.limit,
                    offset=(item.page-1)*item.limit,
                )
            )
            return dataset_description_filter_query
        elif item.node == "manifest":
            if "additional_types" in item.filter:
                manifest_query = self.convert_query(
                    item,
                    query.manifest(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        quick_search=item.search,
                        additional_types=item.filter["additional_types"]
                    )
                )
            else:
                manifest_query = self.convert_query(
                    item,
                    query.manifest(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        quick_search=item.search
                    )
                )
            return manifest_query
        elif item.node == "manifest_filter":
            if "additional_types" in item.filter:
                manifest_filter_query = self.convert_query(
                    item,
                    query.manifestFilter(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        additional_types=item.filter["additional_types"]
                    )
                )
            return manifest_filter_query
        elif item.node == "case":
            if "species" in item.filter:
                case_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        species=item.filter["species"]
                    )
                )
            elif "sex" in item.filter:
                case_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        sex=item.filter["sex"]
                    )
                )
            elif "age_category" in item.filter:
                case_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        age_category=item.filter["age_category"]
                    )
                )
            else:
                case_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit
                    )
                )
            return case_query
        elif item.node == "case_filter":
            if "species" in item.filter:
                case_filter_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        species=item.filter["species"]
                    )
                )
            elif "sex" in item.filter:
                case_filter_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        sex=item.filter["sex"]
                    )
                )
            elif "age_category" in item.filter:
                case_filter_query = self.convert_query(
                    item,
                    query.case(
                        first=item.limit,
                        offset=(item.page-1)*item.limit,
                        age_category=item.filter["age_category"]
                    )
                )
            return case_filter_query
        else:
            raise HTTPException(status_code=NOT_FOUND,
                                detail="GraphQL query cannot be generated by sgqlc")

    def get_queried_result(self, item, SUBMISSION):
        if item.node == None:
            raise HTTPException(status_code=BAD_REQUEST,
                                detail="Missing one or more fields in the request body")

        query = self.generate_query(item)
        try:
            query_result = SUBMISSION.query(query)["data"]
        except Exception as e:
            raise HTTPException(status_code=NOT_FOUND, detail=str(e))

        if query_result[item.node] != []:
            return query_result
        else:
            raise HTTPException(status_code=NOT_FOUND,
                                detail="Data cannot be found in the node")
