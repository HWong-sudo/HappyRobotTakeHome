import json
import os
from typing import List, Optional

import requests
from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# --- Configuration ---
# Load environment variables from a .env file for security
class Settings(BaseSettings):
    api_key: str
    fmcsa_api_key: str   

    class Config:
        env_file = ".env"

settings = Settings()
app = FastAPI(
    title="Acme Logistics Load Broker API",
    description="API for carrier verification and load searching.",
    version="1.0.0"
)

sampleLoads= [
    {
      "load_id": "LID-001",
      "origin": "New York, NY",
      "destination": "Chicago, IL",
      "pickup_datetime": "2025-09-29T09:00:00Z",
      "delivery_datetime": "2025-09-30T17:00:00Z",
      "equipment_type": "Van",
      "loadboard_rate": 1800.00,
      "notes": "Team drivers preferred for fast delivery.",
      "weight": 42000,
      "commodity_type": "General Freight",
      "num_of_pieces": 1,
      "miles": 790,
      "dimensions": "53ft"
    },
    {
      "load_id": "LID-002",
      "origin": "Dallas, TX",
      "destination": "Los Angeles, CA",
      "pickup_datetime": "2025-09-29T14:00:00Z",
      "delivery_datetime": "2025-10-01T22:00:00Z",
      "equipment_type": "Reefer",
      "loadboard_rate": 2500.00,
      "notes": "Must maintain temperature at 34°F. Produce.",
      "weight": 44000,
      "commodity_type": "Produce",
      "num_of_pieces": 1200,
      "miles": 1435,
      "dimensions": "53ft"
    },
    {
      "load_id": "LID-003",
      "origin": "New York, NY",
      "destination": "Miami, FL",
      "pickup_datetime": "2025-09-30T11:00:00Z",
      "delivery_datetime": "2025-10-02T15:00:00Z",
      "equipment_type": "Van",
      "loadboard_rate": 2100.00,
      "notes": "No touch freight. Drop and hook at destination.",
      "weight": 38000,
      "commodity_type": "Electronics",
      "num_of_pieces": 800,
      "miles": 1285,
      "dimensions": "53ft"
    }
  ]

security_scheme = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)):
    """Dependency to validate the API key from the Authorization header."""
    if credentials.scheme != "Bearer" or credentials.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return True

# Define the structure of a Load, matching the fields in the PDF
class Load(BaseModel):
    load_id: str
    origin: str
    destination: str
    pickup_datetime: str
    delivery_datetime: str
    equipment_type: str
    loadboard_rate: float
    notes: str
    weight: int
    commodity_type: str
    num_of_pieces: int
    miles: int
    dimensions: str

class CarrierVerificationRequest(BaseModel):
    mc_number: str = Field(..., description="The carrier's Motor Carrier number.")

class CallLog(BaseModel):
    mc_number: str
    load_id: Optional[str] = None
    outcome: str # "Booked", "Negotiation Failed", "Carrier Ineligible" 
    sentiment: str #"Positive", "Neutral", "Negative" 
    negotiation_rounds: int
    final_rate: Optional[float] = None
    call_duration_seconds: int

# Dummy "Database"
def load_db() -> List[Load]:
    """Loads the list of available loads from the JSON file."""
    try:
        with open("./testData/loads.json", "r") as f:
            data = json.load(f)
        return [Load(**item) for item in data]
    except FileNotFoundError:
        return []

def save_call_log(log: CallLog):
    """Saves the completed call log to a file for dashboard."""
    log_file = "./dashboard/testData/call_logs.json"
    logs = []
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            try:
                logs = json.load(f)
            except json.JSONDecodeError:
                logs = []
    logs.append(log.model_dump())
    with open(log_file, "w") as f:
        json.dump(logs, f, indent=2)


# --- API Endpoints ---
@app.get("/", tags=["General"])
def read_root():
    """A test endpoint to verify if API is running"""
    return {"message": "Welcome to the Acme Logistics API"}

@app.get("/loads",
    response_model=List[Load],
    tags=["Loads"],
    summary="Search for available loads",
    dependencies=[Depends(get_current_user)]
)
def search_loads(
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    equipment_type: Optional[str] = None
):
    """
    Search for loads based on given information
    """
    all_loads = [Load(**item) for item in sampleLoads]
    filtered_loads = all_loads

    if origin:
        filtered_loads = [load for load in filtered_loads if origin.lower() in load.origin.lower()]
    if destination:
        filtered_loads = [load for load in filtered_loads if destination.lower() in load.destination.lower()]
    if equipment_type:
        filtered_loads = [load for load in filtered_loads if equipment_type.lower() == load.equipment_type.lower()]

    if not filtered_loads:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No matching loads found")
   
    return filtered_loads

@app.post("/carrier/verify",
    tags=["Carriers"],
    summary="Verify a carrier's eligibility",
    dependencies=[Depends(get_current_user)]
)
def verify_carrier(request: CarrierVerificationRequest):
    """
    Verifies if a carrier is eligible to work with using the FMCSA API
    """
    mc_number = request.mc_number
    fmcsa_url = f"https://mobile.fmcsa.dot.gov/qc/services/carriers/{mc_number}?webkey={settings.fmcsa_api_key}"
    try:
        response = requests.get(fmcsa_url)
        response.raise_for_status()
       
        data = response.json()
        is_active = data.get("content", [{}])[0].get("carrier", {}).get("carrierOperation", {}).get("carrierOperation", "N") != "OUT-OF-SERVICE"

        if is_active:
            return {"eligible": True, "detail": "Carrier is active and eligible"}
        else:
            return {"eligible": False, "detail": "Carrier is not active or out of service"}

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"eligible": False, "detail": "Carrier not found."}
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="FMCSA API service is currently unavailable")
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not connect to the FMCSA API.")

@app.post("/call-log",
    status_code=status.HTTP_201_CREATED,
    tags=["Reporting"],
    summary="Log the details of a completed call",
    dependencies=[Depends(get_current_user)]
)
def create_call_log(log: CallLog):
    """
    Receives and stores the extracted data from a completed call
    """
    save_call_log(log)
    return {"message": "Call log saved successfully"}

# --- Main Execution Block ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)