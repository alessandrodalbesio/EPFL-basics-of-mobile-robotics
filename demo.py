##### Modules import #####
from tdmclient import ClientAsync # Import thymio library
from time import * # Import time
import traceback # Import traceback
from utils.settings import * # Import settings
from global_navigation.main import * # Import global navigation
from local_navigation.main import * # Import local navigation
from vision.camera import * # Import camera
from vision.map import * # Import map library
from filtering.kalman_filter import * # Import Kalman filter


async def demo():
    ##### Variables definition #####
    start = goal = None
    robotPos_estimated = robotOrientation_estimated = None
    time_start = time()
    motors_speed = lambda lm, rm: { "motor.left.target": [round(lm)], "motor.right.target": [round(rm)] }
    #Wheel Speed Measures
    d_wl = d_wr = 0

    try:
        ##### Connection to the robot #####
        client = ClientAsync()
        node = await client.wait_for_node()
        await node.lock()

        ##### Map creation and variables definition #####
        cam = Camera(save_video=True)
        map = Map(cam, number_of_obstacles=NUMBER_OF_OBSTACLES, robot_size=ROBOT_SIZE)
        map.findObstacles()
        glob = Global(map.obstacles)
        local = Local()
        cam.obstacles = map.obstacles
        angle_hist = []
        pos_hist = []

        ##### Loop #####
        while not glob.goal_reached:        
            # Final and initial position estimation and path planning
            if start is None or goal is None:
                # Find initial and final positions with the camera
                start, initialOrientation, goal = map.getInitialFinalData()
                
                # Find the optimal path with global navigation
                glob.find_optimal_path(start, goal)
                
                # Initialize the kalman filter
                kalman = KalmanFilter(map.convertToCm([start[0], start[1], 0])[0], (initialOrientation+2*np.pi) % 2*np.pi)
                robotPos_estimated = np.array([start[0], start[1]])
                # Define attributes for the real time camera display
                cam.startPosition = start
                cam.goalPosition = goal
                cam.optimalPath = glob.optimal_path

                # Define variables for sampling
                time_last_sample = time()
                time_sampling = None
                
                # Skip the first iteration
                continue

            # Sensing
            await node.wait_for_variables()
            prox_horizontal_measured = node["prox.horizontal"]
            left_speed_measured = node["motor.left.speed"]
            right_speed_measured = node["motor.right.speed"]
            robotPos_measured, robotPos_measured_cm, cameraOrientation_measured = map.cameraRobotSensing() # Robot position and orientation from the camera

            if((robotPos_measured is None) or (cameraOrientation_measured is None)):
                camera_state = 'off'
            else:
                camera_state = 'on'


            # Kidnapping management
            if robotPos_estimated is not None and robotPos_measured is not None and np.linalg.norm(robotPos_measured-robotPos_estimated) > KIDNAPPING_THRESH:
                # Turn off the motors
                await node.set_variables(motors_speed(0,0))

                # Force a new path planning
                start = None
                goal = None

                # Wait for some time (just for visual feedback)
                await client.sleep(SLEEP_TIME_AFTER_KIDNAPPING)

                # Skip the rest of the loop
                continue
            else:
                # Position estimation
                time_sampling = time() - time_last_sample

                if((cameraPos_measured is None) or (cameraOrientation_measured is None)):
                    cam_x = cam_y = cam_theta = -1
                    camera_state = 'off'
                else:
                    if(camera_state != 'off' ):
                        robotPos_measured_cm, cameraOrientation_measured_rad = map.cameraRobotSensing(isInCm=True) # Robot position and orientation from the camera
                        if(not(robotPos_measured_cm is None) and not(cameraOrientation_measured_rad is None)):
                            cam_x = robotPos_measured_cm[0]
                            cam_y = robotPos_measured_cm[1]
                        else:
                            cam_x = cam_y = -1
                            camera_state = 'off'
                        if(not(cameraOrientation_measured_rad is None)):
                            if((cameraOrientation_measured_rad > np.pi)):
                                cam_theta = cameraOrientation_measured_rad - 2*np.pi
                            else:
                                cam_theta = cameraOrientation_measured_rad
                time_sampling = time() - time_last_sample
                [pos_estimated_x, pos_estimated_y, pos_estimated_theta, sp_estimated_lw, sp_estimated_rw] = kalman.update_kalman(d_wl, d_wr, left_speed_measured, right_speed_measured, camera_state, time_sampling, np.array([cam_x, cam_y, cam_theta]))
                robotPos_estimated = np.array([pos_estimated_x, pos_estimated_y])
                robotPos_estimated = map.convertToPx([robotPos_estimated])[0]
                robotOrientation_estimated = pos_estimated_theta
                if(robotOrientation_estimated < 0):
                    robotOrientation_estimated = robotOrientation_estimated + 2*np.pi
                angle_hist.append(robotOrientation_estimated)
                pos_hist.append(robotPos_estimated)
                if(len(angle_hist) > 5):
                    angle_hist.pop(0)
                if(len(pos_hist) > 5):
                    pos_hist.pop(0)

                # Control
                    
                if(cameraPos_measured is None or cameraOrientation_measured is None):
                    cameraPos_measured = robotPos_estimated
                    robotOrientation_estimated = np.mean(angle_hist)
                    
                
                angle_goal = glob.compute_angle_traj(robotPos_estimated)
                if local.local_obstacle(prox_horizontal_measured):
                    motorLeft,motorRight = local.local_controller(prox_horizontal_measured, robotOrientation_estimated, angle_goal)
                    glob.local_goal_point_update(robotPos_estimated)
                else:
                    motorLeft = motorRight = 0
                    if not (robotPos_estimated is None or robotOrientation_estimated is None):
                        motorLeft,motorRight = glob.global_controller(robotPos_estimated, robotOrientation_estimated, left_speed_measured, right_speed_measured)
                d_wl = left_speed_measured - sp_estimated_lw
                d_wr = right_speed_measured - sp_estimated_rw
                # Actuation
                await node.set_variables(motors_speed(motorLeft,motorRight))
                time_last_sample = time()

            # Update the camera attributes
            cam.robotMeasuredPosition = robotPos_measured
            cam.robotMeasuredOrientation = cameraOrientation_measured
            cam.robotEstimatedPosition = robotPos_estimated
            cam.robotEstimatedOrientation = robotOrientation_estimated
            
            # Display the real time data
            if(cam.display()):
                break
            await client.sleep(0.1)
    except Exception as e:
        traceback.print_exc()
    finally:
        # Stop the robot
        await node.set_variables(motors_speed(0,0))

        # Unlock the robot
        await node.unlock()

        # Turn off the camera
        cam.release()
        cv2.destroyAllWindows()