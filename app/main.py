import io
import re
import time
import mimetypes


from fastapi_utils.tasks import repeat_every
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse, JSONResponse, Response
from gen3.auth import Gen3Auth
from gen3.submission import Gen3Submission
from irods.session import iRODSSession
from pyorthanc import Orthanc

from app.config import *
from app.data_schema import *
from app.filter_generator import FilterGenerator
from app.filter import Filter
from app.pagination_format import PaginationFormat
from app.pagination import Pagination
from app.search import Search
from app.sgqlc import SimpleGraphQLClient
from middleware.auth import Authenticator

description = """
## Gen3

You will be able to:

* **Get Gen3 program/project**
* **Get Gen3 node dictionary**
* **Get Gen3 record(s) metadata**
* **Use GraphQL query Gen3 metadata**
* **Download Gen3 file**

## iRODS

You will be able to:

* **Get iRODS root/sub-folder(s)/sub-file(s)**
* **Download iRODS file**
"""

tags_metadata = [
    {
        "name": "Gen3",
        "description": "Gen3 is a data platform for building data commons and data ecosystems",
        "externalDocs": {
            "description": "Gen3 official website",
            "url": "https://gen3.org/",
        },
    },
    {
        "name": "iRODS",
        "description": "iRODS is an open source data management software",
        "externalDocs": {
            "description": "iRODS official website",
            "url": "https://irods.org/",
        },
    },
]

app = FastAPI(
    title="12 Labours Portal",
    description=description,
    # version="",
    # terms_of_service="",
    contact={
        "name": "Auckland Bioengineering Institute",
        "url": "https://www.auckland.ac.nz/en/abi.html",
        # "email": "bioeng-enquiries@auckland.ac.nz",
    },
    # license_info={
    #     "name": "",
    #     "url": "",
    # }
    openapi_tags=tags_metadata,
)

# Cross orgins, allow any for now
origins = [
    '*',
]

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUBMISSION = None
SESSION = None
ORTHANC = None
FILTER_GENERATED = False
fg = None
f = None
pf = None
p = None
s = None
sgqlc = None
a = Authenticator()


def check_irods_status():
    try:
        SESSION.collections.get(iRODSConfig.IRODS_ROOT_PATH)
        return True
    except Exception:
        print("Encounter an error while creating or using the session connection.")
        return False


@ app.on_event("startup")
async def start_up():
    try:
        global SUBMISSION
        GEN3_CREDENTIALS = {
            "api_key": Gen3Config.GEN3_API_KEY,
            "key_id": Gen3Config.GEN3_KEY_ID
        }
        AUTH = Gen3Auth(endpoint=Gen3Config.GEN3_ENDPOINT_URL,
                        refresh_token=GEN3_CREDENTIALS)
        SUBMISSION = Gen3Submission(AUTH)
    except Exception:
        print("Encounter an error while creating the GEN3 auth.")

    try:
        # This function is used to connect to the iRODS server, it requires "host", "port", "user", "password" and "zone" environment variables.
        global SESSION
        SESSION = iRODSSession(host=iRODSConfig.IRODS_HOST,
                               port=iRODSConfig.IRODS_PORT,
                               user=iRODSConfig.IRODS_USER,
                               password=iRODSConfig.IRODS_PASSWORD,
                               zone=iRODSConfig.IRODS_ZONE)
        # SESSION.connection_timeout =
        check_irods_status()
    except Exception:
        print("Encounter an error while creating the iRODS session.")

    try:
        global ORTHANC
        ORTHANC = Orthanc(OrthancConfig.ORTHANC_ENDPOINT_URL,
                          username=OrthancConfig.ORTHANC_USERNAME,
                          password=OrthancConfig.ORTHANC_PASSWORD)
    except Exception:
        print("Encounter an error while creating the Orthanc client.")

    global s, sgqlc, fg, pf, f, p
    s = Search(SESSION)
    sgqlc = SimpleGraphQLClient(SUBMISSION)
    fg = FilterGenerator(sgqlc)
    pf = PaginationFormat(fg)
    f = Filter(fg)
    p = Pagination(fg, f, s, sgqlc)


@ app.on_event("startup")
@repeat_every(seconds=60*60*24)
def periodic_execution():
    global FILTER_GENERATED
    FILTER_GENERATED = False
    while not FILTER_GENERATED:
        FILTER_GENERATED = fg.generate_filter_dictionary()
        if FILTER_GENERATED:
            print("Default filter dictionary has been updated.")

    a.cleanup_authorized_user()


@ app.get("/", tags=["Root"], response_class=PlainTextResponse)
async def root():
    return "This is the fastapi backend."


#########################
### Gen3              ###
### Gen3 Data Commons ###
#########################


def split_access(access):
    access_list = access[0].split("-")
    return access_list[0], access_list[1]


@ app.post("/access/token", tags=["Access"], summary="Create gen3 access token for authorized user", responses=access_token_responses)
async def create_gen3_access(item: IdentityItem, connected: bool = Depends(check_irods_status)):
    if item.identity == None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Missing field in the request body")
    if not connected:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Please check the irods server status or environment variables")

    result = {
        "identity": item.identity,
        "access_token": a.generate_access_token(item.identity, SUBMISSION, SESSION)
    }
    return result


@ app.delete("/access/revoke", tags=["Access"], summary="Revoke gen3 access for authorized user", responses=access_revoke_responses)
async def revoke_gen3_access(is_revoked: bool = Depends(a.revoke_user_authority)):
    if is_revoked:
        raise HTTPException(status_code=status.HTTP_200_OK,
                            detail="Revoke access successfully")


@ app.post("/dictionary", tags=["Gen3"], summary="Get gen3 dictionary information", responses=dictionary_responses)
async def get_gen3_dictionary(item: AccessItem):
    """
    Return all dictionary nodes from the Gen3 Data Commons
    """
    try:
        program, project = split_access(item.access)
        dictionary = SUBMISSION.get_project_dictionary(program, project)
        name_dict = {"dictionary": []}
        for ele in dictionary["links"]:
            ele = ele.replace(
                f"/v0/submission/{program}/{project}/_dictionary/", "")
            name_dict["dictionary"].append(ele)
        return name_dict
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Program {program} or project {project} not found")


@ app.post("/records/{node}", tags=["Gen3"], summary="Get gen3 node records information", responses=records_responses)
async def get_gen3_node_records(node: NodeParam, item: AccessItem):
    """
    Return all records information in a dictionary node.

    - **node**: The dictionary node to export.
    """
    program, project = split_access(item.access)
    node_record = SUBMISSION.export_node(program, project, node, "json")
    if "message" in node_record:
        if "unauthorized" in node_record["message"]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=node_record["message"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=node_record["message"])
    elif node_record["data"] == []:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No data found with node type {node} and check if the correct project or node type is used")
    return node_record


@ app.post("/record/{uuid}", tags=["Gen3"], summary="Get gen3 record information", responses=record_responses)
async def get_gen3_record(uuid: str, item: AccessItem):
    """
    Return record information in the Gen3 Data Commons.

    - **uuid**: uuid of the record.
    """
    program, project = split_access(item.access)
    record = SUBMISSION.export_record(program, project, uuid, "json")
    if "message" in record:
        if "unauthorized" in record["message"]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=record["message"])
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=record["message"]+" and check if the correct project or uuid is used")
    return record


@ app.post("/graphql/query", tags=["Gen3"], summary="GraphQL query gen3 information", responses=query_responses)
async def graphql_query(item: GraphQLQueryItem):
    """
    Return queries metadata records. The API uses GraphQL query language.

    **node**
    - experiment_query
    - dataset_description_query
    - manifest_query
    - case_query

    **filter**
    - {"field_name": ["field_value", ...], ...}

    **search**
    - string content,
    - only available in dataset_description/manifest/case nodes
    """
    query_result = sgqlc.get_queried_result(item)
    return query_result[item.node]


@ app.post("/graphql/pagination/", tags=["Gen3"], summary="Display datasets", responses=pagination_responses)
async def graphql_pagination(item: GraphQLPaginationItem, search: str = "", access_scope: list = Depends(a.gain_user_authority)):
    """
    /graphql/pagination/?search=<string>

    Return filtered/searched metadata records. The API uses GraphQL query language.

    - Default page = 1
    - Default limit = 50
    - Default filter = {}
    - Default search = ""
    - Default relation = "and"
    - Default access = gen3 public access repository
    - Default order = "published(asc)"

    **node**
    - experiment_pagination

    **filter(zero or more)** 
    - {"gen3_node>gen3_field": [filter_name,...], ...}

    **search(parameter)**: 
    - string content
    """
    is_public_access_filtered = p.update_pagination_item(
        item, search, access_scope)
    data_count, match_pair = p.get_pagination_count(item)
    query_result = p.get_pagination_data(
        item, match_pair, is_public_access_filtered)
    # If both asc and desc are None, datasets ordered by self-written order function
    if item.asc == None and item.desc == None:
        query_result = sorted(
            query_result, key=lambda dict: item.filter["submitter_id"].index(dict["submitter_id"]))
    result = {
        "items": pf.reconstruct_data_structure(query_result),
        "numberPerPage": item.limit,
        "total": data_count
    }
    return result


@ app.get("/filter/", tags=["Gen3"], summary="Get filter information", responses=filter_responses)
async def get_filter(sidebar: bool, access_scope: list = Depends(a.gain_user_authority)):
    """
    /filter/?sidebar=<boolean>

    Return the support data for portal filters component.

    - **sidebar**: boolean content.
    """
    retry = 0
    # Stop waiting for the filter generator after hitting the retry limits
    # The retry limit here may need to be increased if there is a large database
    # This also depends on how fast the filter will be generated
    while retry < 12 and not FILTER_GENERATED:
        retry += 1
        time.sleep(retry)
    if not FILTER_GENERATED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Failed to generate filter or the maximum retry limit was reached")

    if sidebar == True:
        return fg.generate_sidebar_filter_information(access_scope)
    else:
        return fg.generate_filter_information(access_scope)


@ app.get("/metadata/download/{program}/{project}/{uuid}/{format}", tags=["Gen3"], summary="Download gen3 record information", response_description="Successfully return a JSON or CSV file contains the metadata")
async def download_gen3_metadata_file(program: str, project: str, uuid: str, format: FormatParam):
    """
    Return a single metadata file for a given uuid.

    - **program**: program name.
    - **project**: project name.
    - **uuid**: uuid of the file.
    - **format**: file format (must be one of the following: json, tsv).
    """
    try:
        metadata = SUBMISSION.export_record(program, project, uuid, format)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if "message" in metadata:
        if "unauthorized" in metadata["message"]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=metadata["message"])
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=metadata["message"]+" and check if the correct project or uuid is used")

    if format == "json":
        return JSONResponse(content=metadata[0],
                            media_type="application/json",
                            headers={"Content-Disposition":
                                     f"attachment;filename={uuid}.json"})
    elif format == "tsv":
        return Response(content=metadata,
                        media_type="text/csv",
                        headers={"Content-Disposition":
                                 f"attachment;filename={uuid}.csv"})


############################################
### iRODS                                ###
### Integrated Rule-Oriented Data System ###
############################################


def generate_collection_list(data):
    collection_list = []
    for ele in data:
        collection_list.append({
            "name": ele.name,
            "path": re.sub(iRODSConfig.IRODS_ROOT_PATH, '', ele.path)
        })
    return collection_list


@ app.post("/collection", tags=["iRODS"], summary="Get folder information", responses=sub_responses)
async def get_irods_collection(item: CollectionItem, connected: bool = Depends(check_irods_status)):
    """
    Return all collections from the required folder.

    Root folder will be returned if no item or "/" is passed.
    """
    if not re.match("(/(.)*)+", item.path):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid path format is used")
    if not connected:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Please check the irods server status or environment variables")

    try:
        collect = SESSION.collections.get(
            iRODSConfig.IRODS_ROOT_PATH + item.path)
        folder_list = generate_collection_list(collect.subcollections)
        file_list = generate_collection_list(collect.data_objects)
        result = {
            "folders": folder_list,
            "files": file_list
        }
        return result
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Data not found in the provided path")


@ app.get("/data/{action}/{filepath:path}", tags=["iRODS"], summary="Download irods file", response_description="Successfully return a file with data")
async def get_irods_data_file(action: ActionParam, filepath: str, connected: bool = Depends(check_irods_status)):
    """
    Used to preview most types of data files in iRODS (.xlsx and .csv not supported yet).
    OR
    Return a specific download file from iRODS or a preview of most types data.

    - **action**: Action should be either preview or download.
    - **filepath**: Required iRODS file path.
    """
    if not connected:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Please check the irods server status or environment variables")

    chunk_size = 1024*1024*1024
    try:
        file = SESSION.data_objects.get(
            f"{iRODSConfig.IRODS_ROOT_PATH}/{filepath}")
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Data not found in the provided path")

    def iterate_file():
        with file.open("r") as file_like:
            chunk = file_like.read(chunk_size)
            print(chunk)
            while chunk:
                yield chunk
                chunk = file_like.read(chunk_size)
    if action == "preview":
        return StreamingResponse(iterate_file(),
                                 media_type=mimetypes.guess_type(file.name)[0])
    elif action == "download":
        return StreamingResponse(iterate_file(),
                                 media_type=mimetypes.guess_type(file.name)[
            0],
            headers={"Content-Disposition": f"attachment;filename={file.name}"})
    else:
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                            detail="The action is not provided in this API")

####################
### Orthanc      ###
### DICOM serber ###
####################


@ app.get("/instance", tags=["Orthanc"])
async def get_instance_ids():
    instance_ids = []
    try:
        patients_identifiers = ORTHANC.get_patients()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid orthanc username or password are used")

    for patient_identifier in patients_identifiers:
        # To get patient information
        patient_info = ORTHANC.get_patients_id(patient_identifier)
        patient_name = patient_info['MainDicomTags']['PatientName']
        study_identifiers = patient_info['Studies']

    # To get patient's studies identifier and main information
    for study_identifier in study_identifiers:
        # To get Study info
        study_info = ORTHANC.get_studies_id(study_identifier)
        study_date = study_info['MainDicomTags']['StudyDate']
        series_identifiers = study_info['Series']

    # To get study's series identifier and main information
    for series_identifier in series_identifiers:
        # Get series info
        series_info = ORTHANC.get_series_id(series_identifier)
        modality = series_info['MainDicomTags']['Modality']
        SeriesInstanceUID = series_info['MainDicomTags']['SeriesInstanceUID']
        if SeriesInstanceUID == "1.3.6.1.4.1.14519.5.2.1.175414966301645518238419021688341658582":
            instance_identifiers = series_info['Instances']

    # and so on ...
    for instance_identifier in instance_identifiers:
        instance_info = ORTHANC.get_instances_id(instance_identifier)
        instance_ids.append(instance_info["ID"])
    return instance_ids
    
@ app.get("/dicom/{identifier}", tags=["Orthanc"])
async def get_dicom_file(identifier:str):
    instance_file = ORTHANC.get_instances_id_file(identifier)
    dicom_file = io.BytesIO(instance_file)
    chunk_size = 1024
    def iterate_file():
        chunk = dicom_file.read(chunk_size)
        while chunk:
            yield chunk
            chunk = dicom_file.read(chunk_size)
    return StreamingResponse(iterate_file(), media_type="application/dicom")
